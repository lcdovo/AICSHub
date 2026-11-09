"""Reviewed drafts, approvals and safe fallback remain different outcomes."""
import hashlib
import json
from pathlib import Path
import pytest
from scripts.business_acceptance import digest,score_turn,load_cases
from scripts.review_business_refund import require_authorization,load_reviewed
from tests.test_business_acceptance import receipt

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('fault',['none','expanded','revoked','wrong_baseline','changed_case','changed_plan'])
def test_reviewed_continuation_cannot_expand_original_remaining_twenty_one(fault):
    proposal={'max_new_dispatches':21,'baseline_requests':19,'cases':['BA-05','BA-10']}
    proposal['continuation_id']=digest(proposal)
    auth={'confirmed':True,'approved_by':'user','continuation_id':proposal['continuation_id'],
          'max_new_dispatches':21,'user_statement':'synthetic authorization','stream_with_usage_footer_authorized':True}
    if fault=='expanded':auth['max_new_dispatches']=22
    elif fault=='revoked':auth['confirmed']=False
    elif fault=='wrong_baseline':proposal['baseline_requests']=0
    elif fault=='changed_case':proposal['cases'].append('BA-20')
    elif fault=='changed_plan':proposal['max_new_dispatches']=24
    if fault=='none':require_authorization(auth,proposal)
    else:
        with pytest.raises(ValueError):require_authorization(auth,proposal)


def test_safe_handoff_cannot_be_relabelled_as_successful_refund_preview():
    cases,sources=load_reviewed();case=next(c for c in cases if c['case_id']=='BA-05');turn=case['turns'][0]
    original=next(c for c in load_cases()[0] if c['case_id']=='BA-05')
    value=receipt(original,turn)
    value['task_status']='waiting_input';value['item_outcomes']={'g1':'limited'}
    text='建议人工协助；未创建人工请求。'
    value['reply']=text;value['text_review']['reply_sha256']=hashlib.sha256(text.encode()).hexdigest()
    value['source_hashes']={i['id']:i['sha256'] for i in sources['items'] if i['id'] in case['source_ids']}
    result=score_turn(case,turn,value,rehearsal=True,sources=sources)
    assert not result['passed'] and 'wrong_task_stage' in result['issues']


def test_review_contract_does_not_equate_preview_with_verified_quality_or_payment():
    value=json.loads((ROOT/'acceptance/business-v2/refund-contract-reviewed.json').read_text(encoding='utf-8'))
    assert value['safe_handoff_is_preview_success'] is False
    assert value['old_labels_changed'] is False and value['previous_paid_results_relabelled'] is False
    assert value['deploy_after_these_two_cases'] is False
