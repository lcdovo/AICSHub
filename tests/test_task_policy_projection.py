"""Decision inputs must preserve complete policy clauses or refuse before dispatch."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.agent.state import AgentNode
from app.agent.task_loop import TaskDecisionNode, decision_payload
from tests.test_agent_task_loop import request_state, reader, executor, read_and_search
from tests.understanding_wire_fixture import item_wire

ROOT=Path(__file__).resolve().parents[1]


def policy_result():
    sources=json.loads((ROOT/'acceptance/business-v2/refund-sources-candidate.json').read_text(encoding='utf-8'))
    cases=json.loads((ROOT/'acceptance/business-v2/refund-cases-candidate.json').read_text(encoding='utf-8'))['cases']
    case=next(c for c in cases if c['case_id']=='BA-05')
    items=[copy.deepcopy(i) for i in sources['items'] if i['id'] in case['source_ids']]
    return {'success':True,'results':items,'meta':{'score_type':'rrf','knowledge_epoch':sources['epoch']},
            'evidence_bundle':{'schema_version':1,'knowledge_epoch':sources['epoch'],'items':copy.deepcopy(items)}}


async def test_all_six_published_policy_fragments_reach_actual_decision(monkeypatch):
    reader(monkeypatch)
    state,_=request_state()
    result=policy_result();node,_=executor(result=result)
    await read_and_search(state,node)
    before=copy.deepcopy(state.current_evidence())
    model=SimpleNamespace(run_structured=AsyncMock(return_value={'step':{'action':'prepare_refund'}}))
    assert await TaskDecisionNode(lambda:model)(state)==AgentNode.TASK_EXECUTION
    payload=json.loads(model.run_structured.call_args.args[1])
    data=next(x['data'] for x in payload['observations'] if x['capability']=='hybrid_search')
    assert [(i['id'],i['version_id'],i['content']) for i in data['items']]==[
        (i['id'],i['version_id'],i['content']) for i in before]
    assert len(data['items'])==6
    assert state.current_evidence()==before
    assert not state.refund_preview_data() and not state.pending_confirmations


async def test_policy_condition_after_character_600_is_not_cut(monkeypatch):
    reader(monkeypatch)
    result=policy_result()
    text='完整政策背景。'*100+'例外：签收后超过7日不可按退货路径办理。'
    result['results'][0]['content']=text
    result['evidence_bundle']['items'][0]['content']=text
    state,_=request_state();node,_=executor(result=result)
    await read_and_search(state,node)
    data=next(x['data'] for x in decision_payload(state)['observations'] if x['capability']=='hybrid_search')
    assert data['items'][0]['content']==text


async def test_oversize_policy_stops_before_model_instead_of_silently_dropping_clauses(monkeypatch):
    reader(monkeypatch)
    state,_=request_state();node,_=executor(result=policy_result())
    await read_and_search(state,node)
    # The actual retrieved snapshot is oversized. No synthetic summarizer may repair it.
    state.evidence_bundle.items[0]['content']='政策条款。'*4000
    model=SimpleNamespace(run_structured=AsyncMock(return_value={'step':{'action':'prepare_refund'}}))
    assert await TaskDecisionNode(lambda:model)(state)==AgentNode.RESPONSE_GENERATION
    model.run_structured.assert_not_awaited()
    assert state.task.status=='stopped' and not state.refund_preview_data()


@pytest.mark.parametrize('detailed',[False,True])
def test_reviewed_refund_real_api_respects_reason_requirement_before_preview(tmp_path,monkeypatch,detailed):
    from scripts.review_business_refund import load_reviewed
    from scripts.business_api_adapter import api_case
    from scripts.offline_profiles import ACTIVE_CONFIG
    from app.config.config import Settings,settings
    import app.agent.task_loop as task_loop
    config=Settings(_env_file=None,**ACTIVE_CONFIG)
    for key in ACTIVE_CONFIG:monkeypatch.setattr(settings,key,getattr(config,key))
    cases,sources=load_reviewed();case=next(c for c in cases if c['case_id']=='BA-05')
    case=copy.deepcopy(case)
    if not detailed:case['turns'][0]['message']='ORD980105外壳破损，请帮我准备退货退款。'
    captured=json.loads((ROOT/'tests/fixtures/business-refund-handoff-v1.json').read_text(encoding='utf-8'))
    observed=[];original=task_loop.allowed_actions
    def inspect(state):
        actions=original(state)
        if state.current_evidence():
            observed.append({'actions':actions,'parameters':state.current_business_parameters().model_dump(),
                'order':task_loop.current_order(state),'conditions_met':task_loop.conditions_met(state),
                'refund_goal':task_loop.refund_goal(state),'preview_enabled':settings.AGENT_TASK_REFUND_PREVIEW_ENABLED,
                'tool_calls':state.task_runtime.tool_calls,'max_tools':settings.AGENT_TASK_MAX_TOOL_CALLS,
                'actor_id':state.actor_id,'source_message_id':state.source_message_id})
        return actions
    monkeypatch.setattr(task_loop,'allowed_actions',inspect)
    def install(service,patch):
        patch.setattr(service,'is_available',lambda:True)
        async def structured(system,user,schema,**kwargs):
            if 'Understanding' in schema.__name__:
                result=copy.deepcopy(captured[0])
                if detailed:
                    result['parameters']['reason']='收到时商品外壳已经破损'
                    result['items'][0]['text']=case['turns'][0]['message']
                return item_wire(result)
            payload=json.loads(user)
            if not payload['current_knowledge_acquired']:return {'step':{'action':'search_knowledge','knowledge_focus':'refund_policy'}}
            return {'step':{'action':'prepare_refund'}} if detailed else {'step':{'action':'ask_user','question':'reason'}}
        patch.setattr(service,'run_structured',AsyncMock(side_effect=structured))
    with api_case(case,tmp_path,sources,install_model=install) as (send,_):row=send(case['turns'][0])
    if detailed:
        assert observed and 'prepare_refund' in observed[-1]['actions'],json.dumps(observed,ensure_ascii=False)
    else:
        assert not observed  # Deterministic reason prompt now precedes any policy search.
        assert row['task']['waiting_for']=='reason'
    assert row['payload']['task_status']==('waiting_confirmation' if detailed else 'waiting_input'),row
    assert row['effects']=={'simulated_applications':0,'preview_count':1 if detailed else 0}
