import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.config.config import Settings,settings
from app.agent.request_understanding import ItemizedTaskUnderstanding,validate_semantics,route_understanding,understand
from app.agent.state import AgentState
from app.agent.task_loop import enter_task
from scripts.offline_profiles import ACTIVE_CONFIG
from tests.understanding_wire_fixture import item_wire

BASE=Path(__file__).parent / 'fixtures'
MESSAGE='ORD980105外壳破损，请帮我准备退货退款。'

@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    config=Settings(_env_file=None,**ACTIVE_CONFIG)
    for key in ACTIVE_CONFIG:monkeypatch.setattr(settings,key,getattr(config,key))

def payload():return json.loads((BASE/'business-read-plan-invalid.json').read_text(encoding='utf-8'))
def state(message=MESSAGE):return AgentState(session_id='candidate-read-plan',user_id=101,actor_id=101,source_message_id=17,user_message=message,understanding_mode='model',task_loop_enabled=True)

def test_original_captured_reply_remains_semantically_rejected():
    parsed=ItemizedTaskUnderstanding.model_validate(payload())
    with pytest.raises(ValueError,match='read_plan_requires_query'):validate_semantics(parsed,state())

def test_separate_corrected_fixture_still_reads_order_before_preview():
    value=payload();value['read_tasks']=[]
    current=state();parsed=validate_semantics(ItemizedTaskUnderstanding.model_validate(value),current)
    default=route_understanding(current,parsed);enter_task(current,default)
    assert current.task_runtime.next_step.action=='read'
    assert current.task_runtime.next_step.reads[0].order_id=='ORD980105'
    assert not current.tool_calls and not current.pending_confirmations and not current.refund_preview_data()

@pytest.mark.parametrize('invalid',[False,True])
async def test_candidate_prompt_clarifies_contract_without_repairing_output(monkeypatch,invalid):
    import app.agent.request_understanding as module
    prompt=module.ITEM_UNDERSTANDING_PROMPT
    assert '单项无条件退款申请' in prompt and '不输出read_tasks' in prompt
    assert '内部前置读订单登记read_tasks' not in prompt
    value=payload()
    if not invalid:value['read_tasks']=[]
    model=SimpleNamespace(is_available=lambda:True,run_structured=AsyncMock(return_value=copy.deepcopy(value) if invalid else item_wire(value)))
    result=await understand(model,state())
    assert (result is None) is invalid
    model.run_structured.assert_awaited_once()
    assert prompt in model.run_structured.call_args.args[0]
    assert bool(value['read_tasks']) is invalid

@pytest.mark.parametrize('kind',['conditional','explicit_multi_read'])
def test_legitimate_conditional_and_explicit_multi_reads_are_preserved(kind):
    value=payload()
    if kind=='conditional':
        message='如果ORD980105已经签收，就准备退货退款，外壳破损。'
        value['conditions']=[{'check':'order_delivered','source':'current_message'}]
        value['items']=[{'text':message,'kind':'prepare_refund'}]
    else:
        message='查询ORD980105的状态，再准备退货退款，原因是外壳破损。'
        value['items']=[{'text':'查询ORD980105的状态','kind':'query_order'},{'text':'准备退货退款','kind':'prepare_refund'}]
        value['subrequests']=[{'topic':'query_order','request_type':'query'},{'topic':'refund','request_type':'apply'}]
    current=state(message);parsed=validate_semantics(ItemizedTaskUnderstanding.model_validate(value),current)
    enter_task(current,route_understanding(current,parsed))
    assert current.task_runtime.next_step.action=='read'
    assert not current.tool_calls and not current.pending_confirmations and not current.refund_preview_data()
