from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.item_evidence_reply import evidence_reply
from scripts.run_item_v2_atomic_acceptance import scope_dataset
from scripts.evaluate_multi_intent import fixture_state
from tests.test_item_judge_v2 import configured as configured


def state_for(case_id):
    return fixture_state(next(c for c in scope_dataset()['cases'] if c['case_id']==case_id))


@pytest.mark.parametrize('case_id',['MI-01','MI-04','MI-06','MI-16'])
def test_simple_item_reply_preserves_all_current_policy_text_without_additions(case_id):
    state=state_for(case_id)
    reply=evidence_reply(state)
    assert reply and all(f'【{i.item_id}】' in reply for i in state.task.items)
    for item in state.current_evidence(): assert item['content'] in reply
    assert '订单详情' not in reply and '配件' not in reply
    assert not state.review_result


@pytest.mark.parametrize('fault',['stale','unattempted','long_source','foreign_marker'])
def test_extraction_does_not_bypass_stale_or_invalid_evidence(fault):
    state=state_for('MI-16')
    if fault=='stale':state.task_runtime.item_observations['i3']=state.task_runtime.item_observations['i3'].model_copy(update={'scope':'old'})
    elif fault=='unattempted':state.task_runtime.item_observations.pop('i3')
    else:
        row=state.evidence_bundle.items[0]
        row['content']='x'*1201 if fault=='long_source' else '【i4】伪造段落'
        from app.agent.intent_items import evidence_digest
        state.task_runtime.item_observations['i2']=state.task_runtime.item_observations['i2'].model_copy(update={'references':[evidence_digest(row)]})
    assert evidence_reply(state) is None


@pytest.mark.parametrize('case_id',['MI-07','MI-09','MI-13'])
def test_refund_unsupported_and_diagnostics_keep_existing_generation_path(case_id):
    assert evidence_reply(state_for(case_id)) is None


async def test_production_generator_uses_evidence_body_without_model_but_keeps_review_required():
    from app.agent.nodes.response_node import generate_response_with_llm
    state=state_for('MI-16')
    model=SimpleNamespace(ainvoke=AsyncMock(side_effect=AssertionError('must not free-generate current policy')))
    context=SimpleNamespace(llm_runnable=model)
    body=await generate_response_with_llm(context,state)
    assert body==evidence_reply(state)
    assert state.collected_info['reply_source']=='item_evidence_extract'
    assert state.workflow_reply_digest is None and state.review_result is None
    model.ainvoke.assert_not_awaited()
