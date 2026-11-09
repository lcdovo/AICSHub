"""A bounded continuation cannot invent a fresh account or pass safety fallback as a preview."""
import copy
import pytest
from scripts.refund_language_acceptance import require_auth,check_turn,load_cases,digest


@pytest.mark.parametrize('fault',['none','revoked','expanded','different_baseline','changed_plan','no_statement'])
def test_language_authorization_is_finite_bound_and_revocable(fault):
    proposal={'baseline_requests':25,'max_new_dispatches':96}
    proposal['continuation_id']=digest(proposal)
    auth={'confirmed':True,'approved_by':'user','user_statement':'synthetic authorization',
          'continuation_id':proposal['continuation_id'],'max_new_dispatches':96,'stream_with_usage_footer_authorized':True}
    if fault=='revoked':auth['confirmed']=False
    elif fault=='expanded':auth['max_new_dispatches']=97
    elif fault=='different_baseline':proposal['baseline_requests']=0
    elif fault=='changed_plan':proposal['extra']=True
    elif fault=='no_statement':auth['user_statement']=''
    if fault=='none':require_auth(auth,proposal)
    else:
        with pytest.raises(ValueError):require_auth(auth,proposal)


def test_fixed_catalog_is_seven_cases_sixteen_turns_and_ninety_six_operations():
    cases,sources=load_cases()
    assert len(cases)==7 and sum(len(c['turns']) for c in cases)==16
    assert sum(len(c['turns'])*6 for c in cases)==96
    assert len({c['case_id'] for c in cases})==7
    assert all(c['label_origin'].endswith('not_human_gold') for c in cases)
    assert all(set(c['source_ids']).issubset({x['id'] for x in sources['items']}) for c in cases)


@pytest.mark.parametrize('fault',['none','wrong_stage','wrong_wait','extra_application','extra_preview'])
def test_turn_gate_requires_actual_stage_and_precise_effects(fault):
    case=load_cases()[0][0];turn=case['turns'][0]
    row={'payload':{'task_status':'waiting_input'},'task':{'waiting_for':'reason'},
         'effects':{'simulated_applications':0,'preview_count':0}}
    if fault=='wrong_stage':row['payload']['task_status']='completed'
    elif fault=='wrong_wait':row['task']['waiting_for']='human_request'
    elif fault=='extra_application':row['effects']['simulated_applications']=1
    elif fault=='extra_preview':row['effects']['preview_count']=1
    if fault=='none':check_turn(case,turn,row)
    else:
        with pytest.raises(ValueError):check_turn(case,turn,row)


def test_guard_case_never_allows_a_simulated_submission():
    cases,_=load_cases()
    assert all(t['applications']==0 for c in cases[2:] for t in c['turns'])
