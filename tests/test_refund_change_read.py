"""A captured root 'change' cannot suppress an explicit, separately validated read."""
from pathlib import Path
import json
from app.agent.request_understanding import decode_itemized_understanding,validate_semantics,route_understanding
from app.agent.state import AgentState,AgentNode
from app.agent.request_context import build_request_context
import copy
from unittest.mock import AsyncMock
import pytest


def test_captured_change_keeps_root_but_schedules_only_the_explicit_order_read():
    value=json.loads((Path(__file__).parent/'fixtures/refund-change-read-wire.json').read_text(encoding='utf-8'))
    state=AgentState(session_id='s',user_id=101,actor_id=101,source_message_id=17,
        user_message='改成ORD981099，只查这笔，先不要退款。',understanding_mode='model',task_loop_enabled=True)
    state.understanding_context=build_request_context(state)
    result=validate_semantics(decode_itemized_understanding(value),state)
    assert result.request_type=='change'
    assert route_understanding(state,result)==AgentNode.TOOL_EXECUTION
    assert [(r.tool,r.resource_id) for r in state.read_plan]==[('query_order','ORD981099')]
    assert not state.pending_confirmations and not state.refund_preview_data()


@pytest.mark.parametrize('transport',['rest','websocket'])
def test_captured_change_queries_new_order_through_real_api_without_refund(tmp_path,monkeypatch,transport):
    from scripts.refund_language_acceptance import load_cases,positive_understanding
    from scripts.business_api_adapter import api_case
    from scripts.offline_profiles import ACTIVE_CONFIG
    from app.config.config import Settings,settings
    config=Settings(_env_file=None,**ACTIVE_CONFIG)
    for key in ACTIVE_CONFIG:monkeypatch.setattr(settings,key,getattr(config,key))
    cases,sources=load_cases();case=copy.deepcopy(next(c for c in cases if c['case_id']=='LC-05'));case['transport']=transport
    captured=json.loads((Path(__file__).parent/'fixtures/refund-change-read-wire.json').read_text(encoding='utf-8'))
    seen=[]
    def install(service,patch):
        patch.setattr(service,'is_available',lambda:True)
        async def structured(system,user,schema,**kwargs):
            if 'Understanding' in schema.__name__:
                seen.append(schema.__name__)
                return positive_understanding(case,'t1') if len(seen)==1 else copy.deepcopy(captured)
            payload=json.loads(user)
            return {'step':{'action':'prepare_refund'}} if payload['current_knowledge_acquired'] else {
                'step':{'action':'search_knowledge','knowledge_focus':'refund_policy'}}
        patch.setattr(service,'run_structured',AsyncMock(side_effect=structured))
    with api_case(case,tmp_path,sources,install_model=install) as (send,_):
        first=send(case['turns'][0]);assert first['payload']['task_status']=='waiting_confirmation'
        final=send(case['turns'][1])
    assert 'ORD981099' in final['payload']['reply'] and '已签收' in final['payload']['reply'],final
    assert final['effects']=={'simulated_applications':0,'preview_count':1}
