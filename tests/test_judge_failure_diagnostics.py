"""Unknown stays blocked, while its allowlisted failure provenance survives reassessment."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.agent.validation_llm import assess_judgement,unknown_judgement,LLMRAGJudge
from app.config.config import settings
from contextvars import ContextVar


@pytest.mark.parametrize('reason',['judge_timeout','judge_failed_or_invalid','judge_model_budget_exceeded'])
def test_reassessing_unknown_preserves_safe_failure_reason(reason):
    result=assess_judgement(unknown_judgement(reason),response='合成',kb_context=[],contract_version='v2')
    assert result['reason']==reason
    assert result['response_validity']=='unknown' and not result['publish_allowed']


def test_untrusted_arbitrary_reason_is_not_reflected_in_diagnostics():
    result=assess_judgement({'support':'unknown','reason':'private credential text'})
    assert result['reason']=='judge_unknown'


def test_binding_failure_code_survives_publication_reassessment():
    value={**unknown_judgement('judge_v2_contract_or_binding_invalid'),
           'contract_failure_code':'candidate_segments_not_exactly_reviewed'}
    result=assess_judgement(value,response='合成句。',kb_context=[],contract_version='v2')
    assert result['reason']=='judge_v2_contract_or_binding_invalid'
    assert result['contract_failure_code']=='candidate_segments_not_exactly_reviewed'
    assert not result['publish_allowed']


async def test_missing_verdict_retains_local_budget_reason_without_retry(monkeypatch):
    monkeypatch.setattr(settings,'LLM_JUDGE_ENABLED',True)
    monkeypatch.setattr(settings,'LLM_JUDGE_CONTRACT_VERSION','v2')
    service=SimpleNamespace(is_available=lambda:True,run_structured=AsyncMock(return_value=None),
        get_last_call_metadata=lambda:{'failure_code':'request_model_budget_exceeded'})
    result=await LLMRAGJudge(service).judge('合成政策说明','合成问题',[], '')
    assert result['reason']=='judge_model_budget_exceeded'
    assert not result['publish_allowed'];service.run_structured.assert_awaited_once()
    assert len(result['diagnostic']['input_sha256'])==64
    assert result['diagnostic']['stage']=='judge_model_budget_exceeded'
    repeated=assess_judgement(result, response='合成政策说明',kb_context=[],contract_version='v2')
    assert repeated['diagnostic']==result['diagnostic']


async def test_gateway_receipt_records_bounded_redacted_raw_and_parse_failure(monkeypatch):
    from app.services.llm_service import LLMService,ModelConfig
    from app.services.model_gateway import GATEWAY,model_context
    from langchain_core.messages import AIMessage
    monkeypatch.setattr(settings,'LLM_GATEWAY_ENABLED',True)
    service=LLMService()
    raw=AIMessage(content='手机号13812345678 '+('合成内容'*1000),response_metadata={'finish_reason':'stop'})
    monkeypatch.setattr(GATEWAY,'invoke',AsyncMock(return_value={'raw':raw,'parsed':None,'parsing_error':ValueError('invalid')}))
    try:
        async with model_context(101,'synthetic-judge-receipt'):
            with pytest.raises(ValueError,match='structured_contract_invalid'):
                await service._gateway_invoke(ModelConfig('primary'),[],object(),'llm_judge')
        receipt=service.get_last_call_metadata()['judge_receipt']
        assert receipt['parse_failure'] and receipt['parsed'] is None
        assert len(receipt['body_sha256'])==64 and len(receipt['redacted_excerpt'])<=2048
        assert '13812345678' not in receipt['redacted_excerpt']
        assert receipt['finish_reason']=='stop'
    finally:await service.close()


async def test_failed_child_task_preserves_receipt_and_code_without_shared_state(monkeypatch):
    monkeypatch.setattr(settings,'LLM_JUDGE_ENABLED',True)
    monkeypatch.setattr(settings,'LLM_JUDGE_CONTRACT_VERSION','v2')
    metadata=ContextVar('test_failed_judge_metadata',default={})
    async def call(*args,**kwargs):
        metadata.set({'purpose':'llm_judge','failure_code':'structured_output_invalid',
                      'judge_receipt':{'body_sha256':'a'*64,'parse_failure':True}})
        return None
    service=SimpleNamespace(is_available=lambda:True,run_structured=call,get_last_call_metadata=metadata.get)
    import asyncio
    result=await asyncio.create_task(LLMRAGJudge(service).judge('合成回答','合成问题',[],''))
    assert result['reason']=='judge_structured_output_invalid'
    assert result['diagnostic']['receipt']['parse_failure'] is True
    assert metadata.get()=={}  # No mutation of caller/task-local metadata.


@pytest.mark.parametrize("reason,code", [
    ("judge_v2_contract_or_binding_invalid", "private credential text"),
    ("judge_timeout", "candidate_segments_not_exactly_reviewed"),
])
def test_contract_diagnostic_does_not_echo_untrusted_or_unrelated_code(reason, code):
    result = assess_judgement({**unknown_judgement(reason), "contract_failure_code": code})
    assert "contract_failure_code" not in result
    assert not result["publish_allowed"]
