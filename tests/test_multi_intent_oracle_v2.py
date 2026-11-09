from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from scripts.multi_intent_oracle import load_oracle,score


def checks(case):
    return [{'item_id':f'i{i+1}','status':status,'supported':True,'conditions_preserved':True,
             'answerability':case['reference_answerability'][i]}
            for i,status in enumerate(case['good_statuses'])]


def test_reviewed_manifest_keeps_all_old_files_unchanged_and_discloses_label_origin():
    data=load_oracle();base=Path(__file__).resolve().parents[1]/'acceptance/multi-intent'
    assert all(hashlib.sha256((base/p).read_bytes()).hexdigest()==d for p,d in data['parent_sources'].items())
    assert set(data['changed_case_ids'])=={'MI-15','MI-16','V2S-01','V2S-02'}
    assert 'not_independent_human_gold' in data['review_origin']


@pytest.mark.parametrize('case_id',[c['case_id'] for c in load_oracle()['cases']])
def test_each_reference_has_consistent_stage_without_claiming_model_semantics(case_id):
    case=next(c for c in load_oracle()['cases'] if c['case_id']==case_id)
    result=score(case,protocol_valid=True,response_supported=True,item_checks=checks(case),
        task_status=case['expected_task_statuses'][0],require_task=True)
    assert result['passed'],result


def test_generic_freight_limited_answer_passes_but_is_not_all_completed():
    case=next(c for c in load_oracle()['cases'] if c['case_id']=='MI-16')
    result=score(case,protocol_valid=True,response_supported=True,item_checks=checks(case),task_status='waiting_input',require_task=True)
    assert result['passed'] and not result['all_items_completed']
    wrong=checks(case);wrong[2].update(status='answered',answerability='answerable')
    assert not score(case,protocol_valid=True,response_supported=True,item_checks=wrong,task_status='completed',require_task=True)['passed']


@pytest.mark.parametrize('fault',['unsupported','condition','unknown','missing','duplicate','premature_completion'])
def test_partial_success_rule_does_not_relax_actual_semantics_or_protocol(fault):
    case=next(c for c in load_oracle()['cases'] if c['case_id']=='MI-16')
    rows=deepcopy(checks(case));valid=True;supported=True;task='waiting_input'
    if fault=='unsupported':supported=False
    elif fault=='condition':rows[2]['conditions_preserved']=False
    elif fault=='unknown':valid=False
    elif fault=='missing':rows.pop()
    elif fault=='duplicate':rows[-1]=rows[0]
    else:task='completed'
    assert not score(case,protocol_valid=valid,response_supported=supported,item_checks=rows,task_status=task,require_task=True)['passed']
