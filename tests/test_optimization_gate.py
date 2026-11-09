import copy
import pytest
from scripts.optimization_gate import auth,digest,read,DATA


@pytest.mark.parametrize('fault',['none','revoked','expanded','changed_plan','missing_statement'])
def test_step_one_is_finite_revocable_and_plan_bound(fault):
    plan={'max_new':8,'baseline':91};plan['plan_id']=digest(plan)
    value={'confirmed':True,'approved_by':'user','user_statement':'synthetic','max_new':8,'plan_id':plan['plan_id']}
    if fault=='revoked':value['confirmed']=False
    elif fault=='expanded':value['max_new']=9
    elif fault=='changed_plan':plan['baseline']=0
    elif fault=='missing_statement':value['user_statement']=''
    if fault=='none':auth(value,plan)
    else:
        with pytest.raises(ValueError):auth(value,plan)


def test_fixed_positive_negative_material_has_eight_unique_cases():
    material=read(DATA);cases=material['cases']
    assert len(cases)==8 and len({c['case_id'] for c in cases})==8
    assert sum(c['expected_publish'] for c in cases)==4
    assert material['origin']=='assistant_authored_synthetic_not_human_gold'
