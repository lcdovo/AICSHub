"""The new prompt must not reset authorization or historical dispatches."""
import pytest
from scripts.business_acceptance import digest
from scripts.revalidate_business_refund import require_revision_authorization, validate_admission


@pytest.mark.parametrize('fault',['none','modified_plan','revoked','wrong_revision','expanded_budget','no_statement'])
def test_refund_revision_is_fixed_and_revocable(fault):
    proposal={'max_new_dispatches':24,'cases':['BA-05','BA-10']}
    proposal['continuation_id']=digest(proposal)
    auth={'confirmed':True,'approved_by':'user','continuation_id':proposal['continuation_id'],
          'max_new_dispatches':24,'user_statement':'synthetic approval',
          'stream_with_usage_footer_authorized':True}
    if fault=='modified_plan':proposal['cases'].append('BA-20')
    elif fault=='revoked':auth['confirmed']=False
    elif fault=='wrong_revision':auth['continuation_id']='other'
    elif fault=='expanded_budget':auth['max_new_dispatches']=25
    elif fault=='no_statement':auth['user_statement']=''
    if fault=='none':require_revision_authorization(auth,proposal)
    else:
        with pytest.raises(ValueError):require_revision_authorization(auth,proposal)


def test_missing_preflights_cannot_admit_live():
    from scripts.revalidate_business_refund import ROOT,sha
    with pytest.raises(ValueError,match='complete_preflight'):
        validate_admission({'source_fingerprint':'synthetic','executor_sha256':sha(ROOT/'scripts/business_refund_execute.py'),
                            'evidence':[]},{'source_fingerprint':'synthetic'})


def test_executor_reads_old_operations_from_fixed_proposal():
    from scripts.revalidate_business_refund import ROOT
    source=(ROOT/'scripts/business_refund_execute.py').read_text(encoding='utf-8')
    assert "authorization['old_operations']" not in source
    assert "keys = tuple(proposal['old_operations'])" in source
