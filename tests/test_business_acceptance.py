"""Business oracle safety checks; fixtures do not measure live model semantics."""
import hashlib

import pytest
from scripts import business_acceptance as module


def receipt(case, turn):
    text = "合成回执，仅测试评分契约。"
    sources=module.load_cases()[1]
    hashes={r["id"]:r["sha256"] for r in sources["items"]}
    return {"source_hashes":{k:hashes[k] for k in case["source_ids"]},"snapshot_epoch":sources["epoch"],"reply":text,"transport_ok":True,"protocol_valid":True,"expected_fault_handled":True,
        "usage_unknown":False,"task_status":turn["expected_task_states"][0],
        "item_outcomes":{f"g{i}":item["allowed_statuses"][0] for i,item in enumerate(turn["expected_items"],1)},
        "simulated_applications":turn["max_simulated_applications"],"payment_transactions":0,
        "replay_same_run_and_no_dispatch":True,"published_snapshot_matches":True,
        "text_review":{"origin":"synthetic_fixture","reply_sha256":hashlib.sha256(text.encode()).hexdigest(),
            "item_alignment_verified":True,**{key:True for key in module.AXES}}}


@pytest.mark.parametrize("case", module.load_cases()[0], ids=lambda c:c["case_id"])
def test_all_case_turns_have_complete_contract_and_bounded_operations(case):
    for turn in case["turns"]:
        assert module.score_turn(case,turn,receipt(case,turn),rehearsal=True)["passed"]
    assert case["label_origin"] == "assistant_authored_synthetic_not_independent_human_gold"


@pytest.mark.parametrize("fault", ["missing_review","changed_reply","wrong_stage","missing_item",
    "unauthorized_application","payment","unknown_usage","stale_snapshot","inconsistent_replay","bad_conditions"])
def test_oracle_rejects_unsafe_or_incomplete_receipts(fault):
    cases,_=module.load_cases();case=next(c for c in cases if c['case_id']=='BA-14');turn=case['turns'][0]
    value=receipt(case,turn)
    if fault=='missing_review':value.pop('text_review')
    elif fault=='changed_reply':value['reply']+='不同正文'
    elif fault=='wrong_stage':value['task_status']='completed'
    elif fault=='missing_item':value['item_outcomes']={}
    elif fault=='unauthorized_application':value['simulated_applications']=1
    elif fault=='payment':value['payment_transactions']=1
    elif fault=='unknown_usage':value['usage_unknown']=True
    elif fault=='stale_snapshot':value['published_snapshot_matches']=False
    elif fault=='inconsistent_replay':value['replay_same_run_and_no_dispatch']=False
    else:value['text_review']['conditions_and_exceptions_preserved']=False
    assert not module.score_turn(case,turn,value,rehearsal=True)['passed']


def test_correct_limitation_can_pass_without_completed_label():
    cases,_=module.load_cases();case=next(c for c in cases if c['case_id']=='BA-03');turn=case['turns'][0]
    result=module.score_turn(case,turn,receipt(case,turn),rehearsal=True)
    assert result['passed'] and not result['all_items_completed']


def test_fixture_reviews_never_count_as_real_generated_text_review():
    case=module.load_cases()[0][0];turn=case['turns'][0]
    assert not module.score_turn(case,turn,receipt(case,turn),rehearsal=False)['passed']


@pytest.mark.parametrize('mutation',['not_confirmed','different_plan','different_count','missing_statement'])
def test_new_fixed_authorization_is_required(mutation):
    plan={'prepared_id':'synthetic','max_requests':168,'max_input_bytes_reserved':168*24576,'max_output_tokens':168*2048}
    authorization={**plan,'confirmed':True,'approved_by':'user','user_statement':'synthetic test approval only'}
    if mutation=='not_confirmed':authorization['confirmed']=False
    elif mutation=='different_plan':authorization['prepared_id']='other'
    elif mutation=='different_count':authorization['max_requests']=169
    else:authorization['user_statement']=''
    with pytest.raises(ValueError,match='explicit_fixed_budget'):module.require_authorization(plan,authorization)


def test_operation_catalog_is_finite_and_cannot_borrow_old_phase_names():
    ops=module.operations(module.load_cases()[0])
    assert len(ops)==168 and all(k.startswith('BA-') and v==2048 for k,v in ops.items())
    assert all(1<=int(k.rsplit('-',1)[1])<=6 for k in ops)


@pytest.mark.parametrize("fault", ["string_protocol","boolean_count","wrong_epoch","wrong_hash","wrong_item_type"])
def test_untyped_or_unbound_receipts_fail_closed(fault):
    case=module.load_cases()[0][0];turn=case['turns'][0];value=receipt(case,turn)
    if fault=='string_protocol':value['protocol_valid']='true'
    elif fault=='boolean_count':value['simulated_applications']=False
    elif fault=='wrong_epoch':value['snapshot_epoch']+=1
    elif fault=='wrong_hash':value['source_hashes']={}
    else:value['item_outcomes']='answered'
    assert not module.score_turn(case,turn,value,rehearsal=True)['passed']
