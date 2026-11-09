import json

import pytest

from scripts.m5_live_budget import DispatchLedger
from scripts.run_item_v2_atomic_acceptance import ReservedLedger,operations,dataset
from scripts.run_multi_intent_live import TwoAccountLedger


def test_reserved_calls_remain_unusable_and_fall_through_without_changing_account(tmp_path):
    limits={'max_requests':3,'max_input_tokens':10000,'max_output_tokens':1000}
    ops={f'case-{n}':10 for n in range(4)}
    first=DispatchLedger(tmp_path/'old.sqlite','old',limits,ops)
    second=DispatchLedger(tmp_path/'new.sqlite','new',limits,ops)
    capped=ReservedLedger(first,2)
    both=TwoAccountLedger(capped,second)
    body=json.dumps({'max_tokens':10}).encode()
    both.reserve('case-0',body);both.reserve('case-1',body)
    assert first.snapshot()['requests']==1 and second.snapshot()['requests']==1
    assert first.limits==limits and first.snapshot()['limits']==limits
    with pytest.raises(ValueError,match='reserved_calls'):
        capped.reserve('case-2',body)
    with pytest.raises(ValueError,match='already_dispatched'):
        both.reserve('case-0',body)


def test_fixed_real_plan_has_exactly_sixty_eight_operations_and_no_understanding_replay():
    data=dataset();allowed=operations(data)
    assert len(data['cases'])==22 and len(allowed)==68
    assert sum('/good' in key or '/bad' in key for key in allowed)==44
    assert sum('E2E-' in key for key in allowed)==20
    assert sum('DEPLOY-' in key for key in allowed)==4
    assert not any('understanding' in key for key in allowed)


def test_scope_revalidation_reruns_all_judgements_and_all_four_graphs():
    from scripts.run_item_v2_atomic_acceptance import scope_dataset
    allowed=operations(scope_dataset(),'scope-v1/')
    assert len(scope_dataset()['cases'])==24 and len(allowed)==72
    assert sum('/good' in key or '/bad' in key for key in allowed)==48
    assert sum('E2E-' in key for key in allowed)==20
    assert sum('DEPLOY-' in key for key in allowed)==4
    assert len(operations(scope_dataset(),'selectors-v1/'))==72
