"""Explicit current-turn refund language must agree with the final SQL gate."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.agent.nodes.tool_node import is_refund_confirmed,refund_cancelled
from app.agent.state import AgentState,AgentNode
from app.agent.task_loop import TaskDecisionNode
from tests.test_agent_task_loop import request_state,reader,executor
from tests.test_refund_persistence import seed,prepared,args,capability
from tests.test_ticket_idempotency import api_context
from app.agent.tools import ApplyRefundTool
from sqlalchemy import select,func
from app.models.models import RefundApplication
from tests.understanding_wire_fixture import item_wire

CONFIRM=['确认提交这笔退货退款申请。','确认提交这笔退款申请','我确认提交本次退货退款申请！']
CANCEL=['取消刚才的申请，不要提交。','取消本次退款申请。','不要提交这笔退货退款申请。']
REJECT=['不确认提交这笔退货退款申请。','如果已经签收，就确认提交这笔退货退款申请。',
        '他说“确认提交这笔退货退款申请”。','之前确认提交这笔退货退款申请。',
        '确认提交这笔退货退款申请，但先不要提交。','确认提交这笔退货退款申请吗？',
        '确认提交ORD999999这笔退货退款申请。','确认提交这笔退货退款申请，改成只退10元。',
        '确认提交这笔退货退款申请。再退另一单。','确认取消这笔退款申请。',
        '请解释“确认提交这笔退货退款申请”的意思。','我取消刚才的取消，再确认提交。',
        '确认提交这笔退款申请...','取消本次退款申请，但保留申请。']


@pytest.mark.parametrize('message',CONFIRM)
def test_current_explicit_full_sentence_is_confirmation(message):
    assert is_refund_confirmed(AgentState(session_id='s',user_id=101,user_message=message))


@pytest.mark.parametrize('message',CANCEL)
def test_current_explicit_full_sentence_is_cancellation(message):
    state=AgentState(session_id='s',user_id=101,user_message=message)
    assert refund_cancelled(state) and not is_refund_confirmed(state)


@pytest.mark.parametrize('message',REJECT)
def test_negated_conditional_quoted_historical_or_changed_request_cannot_confirm(message):
    state=AgentState(session_id='s',user_id=101,user_message=message)
    assert not is_refund_confirmed(state)
    assert not refund_cancelled(state)


@pytest.mark.parametrize('message',CONFIRM+REJECT)
async def test_sql_uses_actual_persisted_sentence_not_model_or_forged_credential(tmp_path,monkeypatch,message):
    async with api_context(tmp_path/'refund-language.db') as (_,factory):
        monkeypatch.setattr('app.utils.database.async_session',factory)
        await seed(factory,confirmation=message)
        payload=await prepared(factory,args('return' if '退货退款' in message else 'refund'))
        result=await ApplyRefundTool().execute(**payload,authorization=capability(payload))
        assert result['success'] is (message in CONFIRM)
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(RefundApplication))==(1 if message in CONFIRM else 0)
        if message in CONFIRM:assert result['data']['status']=='pending_review' and result['data']['is_simulated']


async def test_short_refund_reason_deterministically_asks_before_planning_or_preview(monkeypatch):
    reader(monkeypatch)
    state,_=request_state(conditions=[],parameters={'reason':'外壳破损','refund_type':'return'},
                          message='ORD960001外壳破损，请准备退货退款。')
    node,_=executor()
    await node(state)
    model=SimpleNamespace(run_structured=AsyncMock(return_value={'step':{'action':'suggest_handoff'}}))
    assert await TaskDecisionNode(lambda:model)(state)==AgentNode.TASK_EXECUTION
    model.run_structured.assert_not_awaited()
    assert state.task_runtime.next_step.action=='ask_user'
    assert state.task_runtime.next_step.question=='reason'
    await node(state)
    assert state.task.status=='waiting_input' and state.task.waiting_for=='reason'
    assert not state.refund_preview_data() and not state.has_current_refund_confirmation()


@pytest.mark.parametrize('kind',['refund','exchange'])
async def test_return_sentence_cannot_authorize_different_preview_type(tmp_path,monkeypatch,kind):
    async with api_context(tmp_path/'wrong-kind.db') as (_,factory):
        monkeypatch.setattr('app.utils.database.async_session',factory)
        await seed(factory,confirmation=CONFIRM[0])
        payload=await prepared(factory,args(kind))
        result=await ApplyRefundTool().execute(**payload,authorization=capability(payload))
        assert not result['success']
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(RefundApplication))==0


@pytest.mark.parametrize('transport',['rest','websocket'])
@pytest.mark.parametrize('decision',CONFIRM[:1]+CANCEL[:1]+REJECT[:4])
def test_actual_api_short_reason_followup_and_current_sentence_only(tmp_path,monkeypatch,transport,decision):
    from scripts.review_business_refund import load_reviewed
    from scripts.business_api_adapter import api_case
    from scripts.offline_profiles import ACTIVE_CONFIG
    from app.config.config import Settings,settings
    from app.agent.refund_authorization import is_refund_confirmation_message,is_refund_cancellation_message
    config=Settings(_env_file=None,**ACTIVE_CONFIG)
    for key in ACTIVE_CONFIG:monkeypatch.setattr(settings,key,getattr(config,key))
    cases,sources=load_reviewed();case=copy.deepcopy(next(c for c in cases if c['case_id']=='BA-05'))
    case['transport']=transport
    captured=json.loads((Path(__file__).parent/'fixtures/business-refund-handoff-v1.json').read_text(encoding='utf-8'))[0]
    understands=[];decisions=[]
    def install(service,patch):
        patch.setattr(service,'is_available',lambda:True)
        async def structured(system,user,schema,**kwargs):
            payload=json.loads(user)
            if 'Understanding' in schema.__name__:
                understands.append(payload)
                if len(understands)>2:
                    # A provider cannot use the next bare confirmation to resurrect
                    # a cancelled conditional draft from history.
                    value=copy.deepcopy(captured)
                    value['parameters']['reason']='收到时商品外壳已经破损'
                    value['entity_source']='history';value['continue_task']=False
                    value['items'][0]['text']='准备退货退款'
                    return item_wire(value)
                value=copy.deepcopy(captured)
                if len(understands)==2:
                    value['parameters']['reason']='收到时商品外壳已经破损'
                    value['entity_source']='history';value['continue_task']=True
                    value['items'][0]['text']='收到时商品外壳已经破损'
                return item_wire(value)
            decisions.append(payload)
            return {'step':{'action':'prepare_refund'}} if payload['current_knowledge_acquired'] else {
                'step':{'action':'search_knowledge','knowledge_focus':'refund_policy'}}
        patch.setattr(service,'run_structured',AsyncMock(side_effect=structured))
    with api_case(case,tmp_path,sources,install_model=install) as (send,_):
        first=send({'turn_id':'t1','message':'ORD980105外壳破损，请帮我准备退货退款。'})
        assert first['payload']['task_status']=='waiting_input',first
        assert first['task']['waiting_for']=='reason'
        assert '至少6个字' in first['payload']['reply']
        assert not decisions and first['effects']=={'simulated_applications':0,'preview_count':0}
        second=send({'turn_id':'t2','message':'收到时商品外壳已经破损'})
        assert second['payload']['task_status']=='waiting_confirmation',second
        assert second['effects']=={'simulated_applications':0,'preview_count':1}
        final=send({'turn_id':'t3','message':decision})
        confirm=is_refund_confirmation_message(decision);cancel=is_refund_cancellation_message(decision)
        assert final['effects']=={'simulated_applications':1 if confirm else 0,'preview_count':1}
        if confirm:
            assert final['payload']['task_status']=='completed'
            assert '模拟申请待审核' in final['payload']['reply'] and '尚未执行退款支付' in final['payload']['reply']
        elif cancel:assert final['payload']['task_status']=='cancelled'
        # Explicit confirmation/cancellation bypasses interpretation; rejected sentences cannot submit even if interpretation is unavailable.
        assert len(understands)==2  # Ambiguous current-preview confirmation must not be reinterpreted as a fresh action.
        if not confirm and not cancel:
            assert ('原预览已撤销' if decision.startswith('如果') else '当前预览仍待确认') in final['payload']['reply']
            assert '没有可核实' not in final['payload']['reply']
        if decision.startswith('如果'):
            assert final['payload']['task_status']=='cancelled'
            after=send({'turn_id':'t4','message':'确认'})
            assert after['effects']['simulated_applications']==0
            assert after['effects']['preview_count']==1
            assert len(understands)==2
