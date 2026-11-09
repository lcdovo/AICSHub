import json
from pathlib import Path
import pytest
from app.agent.judge_segment_wire import segment_contract,to_internal
from app.agent.judge_contracts import assess_v2,JudgeContractError


def test_actual_captured_overlap_remains_rejected():
    value=json.loads((Path(__file__).parent/'fixtures/judge-captured-overlap-20261108.json').read_text(encoding='utf-8'))
    ids={i['segment_id'] for i in value['claims']}&{i['segment_id'] for i in value['nonfact_segments']}
    assert ids=={7,8}
    with pytest.raises(JudgeContractError,match='candidate_segments_not_exactly_reviewed'):
        assess_v2(value,'\n'.join(x['text'] for x in value['claims']),[],'','')


def wire():
    return {'schema_version':2,'answerability':'partial','response_behavior':'answer_with_limits','response_validity':'valid',
        'claims_complete':True,'segments':{'s0':{'kind':'fact','support':'supported','evidence':[{'kind':'knowledge','source_id':'K','version_id':'v','quote':'原盒损坏可用厚纸箱。'}],'issues':[]},'s1':{'kind':'clarification'}},'response_issues':[]}


def test_exclusive_wire_binds_text_and_retains_evidence_validation():
    text='原盒损坏可用厚纸箱。请提供型号。'
    result=to_internal(wire(),text)
    assert len(result.claims)==1 and result.claims[0].text=='原盒损坏可用厚纸箱。'
    assert result.nonfact_segments[0].segment_id==1
    assert assess_v2(result.model_dump(),text,[{'id':'K','version_id':'v','content':'原盒损坏可用厚纸箱。'}],'','')['publish_allowed']
    value=wire();value['segments']['s0']['evidence'][0]['quote']='拼接错误证据'
    with pytest.raises(JudgeContractError):assess_v2(to_internal(value,text).model_dump(),text,[{'id':'K','version_id':'v','content':'原盒损坏可用厚纸箱。'}],'','')


@pytest.mark.parametrize('fault',['duplicate_channel','missing_slot','wrong_slot','fact_fields_on_nonfact'])
def test_wire_never_repairs_old_or_ambiguous_response(fault):
    value=wire()
    if fault=='duplicate_channel':value['claims']=[]
    elif fault=='missing_slot':del value['segments']['s1']
    elif fault=='wrong_slot':value['segments']['s2']=value['segments'].pop('s1')
    else:value['segments']['s1']['evidence']=[]
    with pytest.raises(ValueError):to_internal(value,'原盒损坏可用厚纸箱。请提供型号。')


@pytest.mark.parametrize('fault',['identity','version','quote','no_evidence','unsupported'])
def test_exclusive_classification_never_grants_unsupported_fact(fault):
    value=wire();claim=value['segments']['s0']
    if fault=='identity':claim['evidence'][0]['source_id']='foreign'
    elif fault=='version':claim['evidence'][0]['version_id']='old'
    elif fault=='quote':claim['evidence'][0]['quote']='原盒损坏一定退款'
    elif fault=='no_evidence':claim['evidence']=[]
    else:claim['support']='unsupported'
    internal=to_internal(value,'原盒损坏可用厚纸箱。请提供型号。')
    if fault in {'identity','version','quote'}:
        with pytest.raises(JudgeContractError):
            assess_v2(internal.model_dump(),'原盒损坏可用厚纸箱。请提供型号。',[{'id':'K','version_id':'v','content':'原盒损坏可用厚纸箱。'}])
    else:
        assert not assess_v2(internal.model_dump(),'原盒损坏可用厚纸箱。请提供型号。',[{'id':'K','version_id':'v','content':'原盒损坏可用厚纸箱。'}])['publish_allowed']


async def test_enabled_judge_uses_exclusive_wire_then_original_strict_assessment(monkeypatch):
    from app.config.config import settings
    from app.agent.validation_llm import LLMRAGJudge
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    monkeypatch.setattr(settings,'LLM_JUDGE_ENABLED',True)
    monkeypatch.setattr(settings,'LLM_JUDGE_CONTRACT_VERSION','v2')
    monkeypatch.setattr(settings,'LLM_JUDGE_SEGMENT_WIRE',True)
    service=SimpleNamespace(is_available=lambda:True,run_structured=AsyncMock(return_value=wire()))
    result=await LLMRAGJudge(service).judge('原盒损坏可用厚纸箱。请提供型号。','包装可否替代？',
        [{'id':'K','version_id':'v','content':'原盒损坏可用厚纸箱。'}],'')
    assert result['publish_allowed'] and result['claim_checks'][0]['references_verified']
    assert 'segments' in service.run_structured.call_args.args[2].model_json_schema()['properties']
    service.run_structured.assert_awaited_once()
