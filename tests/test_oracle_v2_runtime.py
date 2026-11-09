import json
from pathlib import Path

import pytest

from scripts.evaluate_multi_intent_oracle_v2 import fixture,valid_fixture,OPS
from scripts.multi_intent_oracle import score
from app.agent.item_evidence_reply import evidence_reply
from app.agent.intent_items import snapshot,verify_review,finalize
from app.agent.item_judge_selectors import selector_contract
from tests.test_item_judge_v2 import assess,configured as configured


@pytest.mark.parametrize('case_id',['KB-PRESENT','KB-MISSING'])
def test_actual_policy_snapshot_keeps_damage_exception_and_correct_task_stage(case_id):
    root=Path(__file__).resolve().parents[1]
    rows=json.loads((root/'acceptance/multi-intent/captured-published-packaging-v1.json').read_bytes())['items']
    material={'retrieval':{'success':True,'results':rows,'meta':{'knowledge_epoch':1,'score_type':'rrf'},
        'evidence_bundle':{'schema_version':1,'items':rows,'knowledge_epoch':1}}}
    case,state=fixture(case_id,material);body=evidence_reply(state)
    assert '如原包装损坏，请使用厚实纸箱' in body
    for row in rows:assert row['content'] in body
    raw=valid_fixture(state,body)
    selector_contract(snapshot(state),body).model_validate(raw)
    judged=assess(state,body,raw);assert judged['publish_allowed']
    reviews,issues=verify_review(state,body,judged['item_checks']);assert not issues
    state.task_runtime.item_reviews=reviews;finalize(state)
    result=score(case,protocol_valid=True,response_supported=True,item_checks=judged['item_checks'],
        task_status=state.task.status,require_task=True)
    assert result['passed']
    assert result['all_items_completed'] is (case_id=='KB-PRESENT')


def test_runtime_operations_are_exactly_two_judge_calls():
    assert len(OPS)==2 and all(key.endswith('/judge') and cap==2048 for key,cap in OPS.items())
