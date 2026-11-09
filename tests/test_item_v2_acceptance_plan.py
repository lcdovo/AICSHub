"""Acceptance preparation stays offline and does not turn fixtures into model quality."""
import json
import pytest

from scripts.prepare_item_v2_acceptance import FRESH,offline_cases,prepare
from scripts.evaluate_multi_intent import isolated_configuration


def test_fresh_cases_are_separately_labeled_and_old_dataset_unchanged():
    from scripts.evaluate_multi_intent import DATA
    import hashlib
    assert hashlib.sha256(DATA.read_bytes()).hexdigest()=='0d88270d3e4789f9beb638a11ae457d4ae1947665b38a750fc86b15a1bc4c98b'
    fresh=json.loads(FRESH.read_bytes())
    assert len(fresh['cases'])==4 and len({case['family'] for case in fresh['cases']})==4
    assert 'not_independent_human_gold' in fresh['review_origin']
    with isolated_configuration(): results=offline_cases()
    assert len(results)==20 and all(row['passed'] for row in results)


def test_plan_requires_new_budget_and_cannot_overwrite_evidence(tmp_path):
    prepare(tmp_path/'prepared')
    value=json.loads((tmp_path/'prepared/plan.json').read_bytes())
    assert value['status']=='PROPOSED_NOT_AUTHORIZED'
    assert 'LLM_MODEL' in value['provider'] and 'LLM_API_KEY' not in value['provider']
    assert value['component_calls']['total']+value['full_graph_max_total']+value['deployed_synthetic_chat_max_http']==80
    assert value['old_accounts_used']==value['old_accounts_limit']==122
    assert value['max_output_reserved_tokens']==163840
    assert value['new_account_required'] and value['old_other_stage_nine_not_reused']
    assert value['stop_on_first_component_or_graph_failure'] and value['unknown_is_failure']
    assert not list(tmp_path.rglob('dispatch.sqlite'))
    with pytest.raises(ValueError,match='preserve_existing_preparation'): prepare(tmp_path/'prepared')


def test_live_acceptance_operations_have_one_fixed_account_and_eighty_request_cap():
    from scripts.run_item_v2_acceptance import limits,dataset
    value=limits(dataset())
    assert len(value)==80 and set(value.values())=={2048}
    assert sum(key.startswith('DEPLOY-') for key in value)==4
    assert sum(key.startswith('E2E-') for key in value)==16
    assert 'E2E-MI-01/http-5' not in value
    revised=limits(dataset(),'json-object-v1/')
    assert len(revised)==160
    assert all(key in revised for key in value)
    assert sum(key.startswith('json-object-v1/') for key in revised)==80
    segmented=limits(dataset(),'safe-id-v1/')
    assert len(segmented)==240
    assert all(key in segmented for key in revised)
    assert sum(key.startswith('safe-id-v1/') for key in segmented)==80
    exclusive=limits(dataset(),'sentences-v1/')
    assert len(exclusive)==320 and all(key in exclusive for key in segmented)
    assert sum(key.startswith('sentences-v1/') for key in exclusive)==80
    failed_read=limits(dataset(),'failed-read-v1/')
    assert len(failed_read)==400 and all(key in failed_read for key in exclusive)
    execution=limits(dataset(),'operation-limit-v1/')
    assert len(execution)==480 and all(key in execution for key in failed_read)
    read_only=limits(dataset(),'read-only-v1/')
    assert len(read_only)==560 and all(key in read_only for key in execution)
    information=limits(dataset(),'information-limit-v1/')
    assert len(information)==640 and all(key in information for key in read_only)
    marker=limits(dataset(),'marker-v1/')
    assert len(marker)==724 and all(key in marker for key in information)
    assert 'marker-v1/E2E-MI-16/http-6' in marker and 'marker-v1/E2E-MI-16/http-7' not in marker
    layout=limits(dataset(),'layout-v1/')
    assert len(layout)==808 and all(key in layout for key in marker)


def test_new_acceptance_authorization_is_required_before_provider_initialization(tmp_path):
    from scripts.run_item_v2_acceptance import validate
    prepare(tmp_path/'prepared')
    with pytest.raises(ValueError,match='new_eighty_request_authorization_required'):
        validate(tmp_path/'prepared/plan.json',None)
    assert not list(tmp_path.rglob('dispatch.sqlite'))
