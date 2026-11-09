"""Real SDK transport checks: output constraint requested, malformed output never repaired."""
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from openai import AsyncOpenAI

from app.config.config import settings
from app.services.llm_service import LLMService,ModelConfig
from app.services.model_gateway import model_context
from app.models.models import ModelCallRecord
from sqlalchemy import select
from tests.test_m4_gateway import factory as factory
from tests.test_item_judge_v2 import fixture
from app.agent.intent_items import REVIEW_CONTEXT,snapshot
from app.agent.item_judge_v2 import to_fragment_verdict,to_sentence_verdict
from app.agent.answer_contracts import review_query,tool_evidence_text
from app.agent.validation_llm import LLMRAGJudge


@pytest.mark.parametrize('fault',['valid','bare_enum','truncated','duplicate_key','markdown','wrong_type','length','tools'])
async def test_json_object_judge_uses_strict_raw_content_parser(factory,monkeypatch,fault):
    for key,value in {'LLM_JUDGE_ENABLED':True,'LLM_JUDGE_CONTRACT_VERSION':'v2',
        'LLM_JUDGE_V2_OUTPUT_PROTOCOL':'json_object','REQUEST_UNDERSTANDING_MODE':'model',
        'AGENT_TASK_LOOP_ENABLED':True,'AGENT_ITEM_COMPLETION_ENABLED':True,
        'LLM_DAILY_USER_TOKEN_BUDGET':1000000}.items():
        monkeypatch.setattr(settings,key,value,raising=False)
    state,body,value=fixture('MI-01')
    wire=to_sentence_verdict(to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence()))
    from app.agent.item_judge_selectors import fixture_selectors
    wire=fixture_selectors(wire,body,snapshot(state),tool_evidence_text(state),state.current_evidence())
    text=json.dumps(wire,ensure_ascii=False)
    if fault=='bare_enum':
        capture=json.loads((Path(__file__).resolve().parents[1]/
            'acceptance/multi-intent/captured-item-v2-invalid-json-v1.json').read_bytes())
        text=capture['arguments']
    elif fault=='truncated': text=text[:-1]
    elif fault=='duplicate_key': text=text.replace('"schema_version": 2','"schema_version": 2, "schema_version": 2')
    elif fault=='markdown': text='```json\n'+text+'\n```'
    elif fault=='wrong_type': text=text.replace('"claims_complete": true','"claims_complete": "true"')
    sent=[]
    def respond(request):
        payload=json.loads(request.content);sent.append(payload)
        assert payload.get('response_format')=={'type':'json_object'}
        assert 'tools' not in payload and 'tool_choice' not in payload
        assert '"schema_version"' in payload['messages'][0]['content']
        assert '"full"' in payload['messages'][0]['content']
        message={'role':'assistant','content':text}
        if fault=='tools': message['tool_calls']=[{'id':'x','type':'function','function':{'name':'unexpected','arguments':'{}'}}]
        return httpx.Response(200,json={'id':'synthetic','object':'chat.completion','created':1,'model':'synthetic-judge',
            'choices':[{'index':0,'finish_reason':'length' if fault=='length' else 'stop','message':message}],
            'usage':{'prompt_tokens':20,'completion_tokens':30,'total_tokens':50}},request=request)
    service=LLMService()
    service.models=[ModelConfig('synthetic',api_key='synthetic-key',api_base='https://offline.invalid/v1',
        model='synthetic-judge',thinking_mode='disabled',structured_output_method='function_calling')]
    service.local_model=None
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as provider:
        original=service._get_chat_model
        def model(config,**kwargs):
            target=original(config,**kwargs)
            sdk=AsyncOpenAI(api_key='synthetic-key',base_url=config.api_base,http_client=provider,max_retries=0)
            target.root_async_client,target.async_client=sdk,sdk.chat.completions
            return target
        token=REVIEW_CONTEXT.set(snapshot(state))
        try:
            with patch.object(service,'_get_chat_model',model):
                async with model_context(101,'json-protocol-'+fault,factory=factory):
                    result=await LLMRAGJudge(service).judge(body,review_query(state),state.current_evidence(),tool_evidence_text(state))
        finally: REVIEW_CONTEXT.reset(token);await service.close()
    assert result['publish_allowed'] is (fault=='valid'),result
    assert len(sent)==1
    async with factory() as db:
        records=(await db.scalars(select(ModelCallRecord))).all()
        assert len(records)==1
        if fault=='length':
            # SDK rejects truncated completions before returning an AIMessage;
            # SQL therefore retains an unknown reservation, not fabricated usage.
            assert records[0].status=='unknown'
        else:
            assert records[0].input_tokens==20 and records[0].output_tokens==30


def test_json_judge_protocol_changes_answer_policy_binding(monkeypatch):
    from app.agent.answer_contracts import policy_fingerprint
    monkeypatch.setattr(settings,'LLM_JUDGE_CONTRACT_VERSION','v2')
    monkeypatch.setattr(settings,'LLM_JUDGE_V2_OUTPUT_PROTOCOL','function_calling')
    original=policy_fingerprint()
    monkeypatch.setattr(settings,'LLM_JUDGE_V2_OUTPUT_PROTOCOL','json_object')
    assert policy_fingerprint()!=original


async def test_json_protocol_switch_does_not_change_v1_judge(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    monkeypatch.setattr(settings,'LLM_JUDGE_CONTRACT_VERSION','v1')
    monkeypatch.setattr(settings,'LLM_JUDGE_V2_OUTPUT_PROTOCOL','json_object')
    monkeypatch.setattr(settings,'LLM_JUDGE_ENABLED',True)
    service=SimpleNamespace(is_available=lambda:True,run_structured=AsyncMock(return_value={
        'grounded':True,'fact_score':1.,'has_unsupported_claim':False,'refuse_recommended':False,'reason':'synthetic'}))
    token=REVIEW_CONTEXT.set(None)
    try: result=await LLMRAGJudge(service).judge('合成回答','合成问题',[{'content':'合成证据'}])
    finally: REVIEW_CONTEXT.reset(token)
    assert result['publish_allowed']
    assert 'strict_json_object' not in service.run_structured.call_args.kwargs
