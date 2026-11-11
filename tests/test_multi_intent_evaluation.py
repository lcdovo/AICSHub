"""冻结材料与授权入口测试；不会调用供应商或更改旧评测账本。"""
import json
from contextlib import closing
from pathlib import Path

import pytest

from scripts.evaluate_multi_intent import DATA,offline,fixture_state,candidate,score_plan
from scripts.run_multi_intent_live import validated_plan,operation_limits
from app.config.config import settings


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings,"REQUEST_UNDERSTANDING_MODE","model")
    monkeypatch.setattr(settings,"AGENT_TASK_LOOP_ENABLED",True)
    monkeypatch.setattr(settings,"AGENT_ITEM_COMPLETION_ENABLED",True)
    monkeypatch.setattr(settings,"USER_MEMORY_ENABLED",False)


def test_synthetic_families_are_distinct_and_partitioned_before_model_results():
    dataset=json.loads(DATA.read_bytes())
    assert len(dataset["cases"])==16 and len({case["family"] for case in dataset["cases"]})==16
    development={case["family"] for case in dataset["cases"] if case["split"]=="development"}
    holdout={case["family"] for case in dataset["cases"] if case["split"]=="holdout"}
    assert len(development)==len(holdout)==8 and development.isdisjoint(holdout)
    assert dataset["review_origin"]=="assistant_curated_synthetic_not_independent_human_gold"


def test_offline_evaluation_does_not_claim_model_semantic_quality():
    report=offline(json.loads(DATA.read_bytes()))
    assert report["passed"] and report["semantic_model_quality"]=="NOT_RUN"
    assert report["provider_http_requests"]==0 and report["semantic_dependence_demonstrated"]>0


def test_all_scenario_plans_pass_current_semantics_and_count_each_item():
    from app.agent.request_understanding import validate_semantics
    dataset=json.loads(DATA.read_bytes())
    for case in dataset["cases"]:
        state=fixture_state(case)
        assert validate_semantics(state.understanding,state) is not None
        assert score_plan(case,state.understanding)["all_items_covered"]
        _,raw=candidate(case)
        assert len(raw)==len(case["items"])


def test_live_operation_budget_cannot_silently_expand():
    operations=operation_limits(json.loads(DATA.read_bytes()))
    assert len(operations)==72 and all(value==1024 for value in operations.values())


def test_resumed_complete_run_is_bounded_and_preserves_unknown_request(tmp_path):
    import sqlite3
    from scripts.m5_live_budget import DispatchLedger
    dataset=json.loads(DATA.read_bytes())
    operations=operation_limits(dataset,"revision-v3/",end_to_end_limit=5,historical_prefixes=["revision-v2/"])
    assert sum(key.startswith("revision-v3/") for key in operations)==68
    assert "revision-v3/E2E-MI-01/http-6" not in operations
    limits={"max_requests":80,"max_input_tokens":80*24576,"max_output_tokens":80*1024}
    ledger=DispatchLedger(tmp_path/"dispatch.sqlite","same-phase",limits,operations)
    body=json.dumps({"max_tokens":500,"messages":[]}).encode()
    ledger.reserve("revision-v2/MI-02/bad",body)
    with closing(sqlite3.connect(ledger.path)) as db, db: before=db.execute("SELECT * FROM dispatches").fetchall()
    ledger.reserve("revision-v3/MI-02/bad",body)
    with pytest.raises(ValueError,match="operation_already_dispatched_no_retry"):
        ledger.reserve("revision-v2/MI-02/bad",body)
    with closing(sqlite3.connect(ledger.path)) as db, db:
        assert db.execute("SELECT * FROM dispatches WHERE operation='revision-v2/MI-02/bad'").fetchall()==before
    assert ledger.snapshot()["requests"]==2 and ledger.snapshot()["usage_unknown_requests"]==2


def test_supplement_uses_separate_account_without_expanding_original(tmp_path):
    from scripts.m5_live_budget import DispatchLedger
    from scripts.run_multi_intent_live import TwoAccountLedger
    operations={key:10 for key in ['old-unknown','new-1','new-2','extra']}
    first=DispatchLedger(tmp_path/'original.sqlite','original',
        {'max_requests':2,'max_input_tokens':1000,'max_output_tokens':20},operations)
    second=DispatchLedger(tmp_path/'supplement.sqlite','supplement',
        {'max_requests':1,'max_input_tokens':1000,'max_output_tokens':10},operations)
    body=json.dumps({'max_tokens':10}).encode()
    marker=first.path.with_suffix('.created.json').read_bytes()
    first.reserve('old-unknown',body)
    combined=TwoAccountLedger(first,second)
    combined.reserve('new-1',body)
    assert first.snapshot()['requests']==2 and second.snapshot()['requests']==0
    combined.reserve('new-2',body)
    assert first.snapshot()['requests']==2 and second.snapshot()['requests']==1
    with pytest.raises(ValueError,match='operation_already_dispatched_no_retry'):
        combined.reserve('old-unknown',body)
    with pytest.raises(ValueError,match='authorized_budget_exhausted'):
        combined.reserve('extra',body)
    assert combined.snapshot()['requests']==3 and combined.snapshot()['usage_unknown_requests']==3
    assert first.path.with_suffix('.created.json').read_bytes()==marker
    assert first.limits['max_requests']==2


@pytest.mark.parametrize('account_count',[3,4])
def test_authorized_accounts_preserve_unknown_calls_and_have_combined_hard_cap(tmp_path,account_count):
    from scripts.m5_live_budget import DispatchLedger
    from scripts.run_multi_intent_live import TwoAccountLedger
    operations={str(index):10 for index in range(account_count+1)}
    accounts=[DispatchLedger(tmp_path/f'{index}.sqlite',str(index),
        {'max_requests':1,'max_input_tokens':1000,'max_output_tokens':10},operations) for index in range(account_count)]
    ledger=accounts[0]
    for account in accounts[1:]: ledger=TwoAccountLedger(ledger,account)
    body=json.dumps({'max_tokens':10}).encode()
    for index in range(account_count): ledger.reserve(str(index),body)
    assert [account.snapshot()['requests'] for account in accounts]==[1]*account_count
    assert ledger.snapshot()['requests']==ledger.limits['max_requests']==account_count
    assert ledger.snapshot()['usage_unknown_requests']==account_count
    with pytest.raises(ValueError,match='authorized_budget_exhausted'): ledger.reserve(str(account_count),body)
    with pytest.raises(ValueError,match='operation_already_dispatched_no_retry'): ledger.reserve('0',body)


def test_unknown_resume_binds_preserved_history_and_explicit_adjudication(tmp_path,monkeypatch):
    import sqlite3
    from scripts.m5_live_budget import DispatchLedger
    from scripts.evaluate_multi_intent import prepare
    dataset=json.loads(DATA.read_bytes())
    limits={"max_requests":80,"max_input_tokens":80*24576,"max_output_tokens":80*1024}
    path=tmp_path/"reports/multi-intent/live-budget/original/dispatch.sqlite"
    ledger=DispatchLedger(path,"original",limits,operation_limits(dataset,"revision-v2/"))
    body=json.dumps({"max_tokens":500,"messages":[]}).encode()
    for prefix in ("","revision-v2/"):
        for case in ("MI-01","MI-02"):
            for stage in ("understanding","good","bad"):
                ledger.reserve(prefix+case+"/"+stage,body)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE dispatches SET status='http_complete' WHERE operation!='revision-v2/MI-02/bad'")
        db.commit()
        before=db.execute("SELECT * FROM dispatches ORDER BY operation").fetchall()
    monkeypatch.setattr("scripts.evaluate_multi_intent.ROOT",tmp_path)
    monkeypatch.setattr("scripts.test_provenance.snapshot_sources",lambda root:{"fingerprint":"frozen"})
    import hashlib
    provider={"synthetic":"unchanged"}
    original={"prepared_id":"original","dataset_sha256":hashlib.sha256(DATA.read_bytes()).hexdigest(),"provider":provider}
    prior=tmp_path/"prior.json";prior.write_text(json.dumps(original))
    with pytest.raises(ValueError,match="unknown_dispatch_requires_adjudication"):
        prepare(tmp_path/"unapproved",dataset,provider=provider,revalidate=prior)
    resumed=prepare(tmp_path/"approved",dataset,provider=provider,revalidate=prior,resume_counted_unknown=True)
    assert resumed["max_planned_new_requests"]==resumed["max_additional_requests"]==68
    assert resumed["unknown_dispatch_adjudication"]["usage_remains_unknown"]
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT * FROM dispatches ORDER BY operation").fetchall()==before
    assert resumed["preserved_operations"]==[row[0] for row in before]
    with pytest.raises(ValueError,match="unused_prefix"):
        prepare(tmp_path/"reuse",dataset,provider=provider,revalidate=prior,resume_counted_unknown=True,
                continuation_prefix="revision-v2/")
    with closing(sqlite3.connect(path)) as db, db:
        for index in range(4):
            db.execute("INSERT INTO dispatches VALUES (?, 1, 1, 'hash', 'unknown_dispatched', NULL, NULL, NULL)",
                       (f"diagnostic-v4/synthetic-{index}",))
        db.commit()
    with pytest.raises(ValueError,match="exceeds_remaining_phase_budget"):
        prepare(tmp_path/"over-budget",dataset,provider=provider,revalidate=prior,resume_counted_unknown=True)
    bounded=prepare(tmp_path/"bounded",dataset,provider=provider,revalidate=prior,resume_counted_unknown=True,
                    continuation_prefix="revision-v4/",end_to_end_limit=4)
    assert bounded["max_planned_new_requests"]==64 and bounded["previous_requests"]==16
    operations=operation_limits(dataset,"revision-v4/",end_to_end_limit=4,
                                preserved_operations=bounded["preserved_operations"])
    assert "diagnostic-v4/synthetic-0" in operations
    assert "revision-v4/E2E-MI-16/http-5" not in operations


def test_revalidation_reuses_original_ledger_without_resetting_rows(tmp_path):
    import sqlite3
    from scripts.m5_live_budget import DispatchLedger
    dataset=json.loads(DATA.read_bytes())
    limits={"max_requests":80,"max_input_tokens":80*24576,"max_output_tokens":80*1024}
    path=tmp_path/"dispatch.sqlite"
    first=DispatchLedger(path,"original-phase",limits,operation_limits(dataset))
    body=json.dumps({"max_tokens":500,"messages":[]}).encode()
    first.reserve("MI-01/understanding",body)
    with closing(sqlite3.connect(path)) as db, db: before=db.execute('SELECT * FROM dispatches').fetchone()
    second=DispatchLedger(path,"original-phase",limits,operation_limits(dataset,"revision-v2/"))
    second.reserve("revision-v2/MI-01/understanding",body)
    assert second.snapshot()["requests"]==2
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT * FROM dispatches WHERE operation='MI-01/understanding'").fetchone()==before
    with pytest.raises(ValueError,match="authorization_changed"):
        DispatchLedger(path,"replacement-phase",limits,operation_limits(dataset,"revision-v2/"))


def test_authorization_required_before_reading_credentials_or_dispatch(tmp_path,monkeypatch):
    import hashlib
    from scripts.test_provenance import snapshot_sources
    root=Path(__file__).resolve().parents[1]
    value={"prepared_id":"synthetic","source_fingerprint":snapshot_sources(root)["fingerprint"],
        "dataset_sha256":hashlib.sha256(DATA.read_bytes()).hexdigest(),"provider":{},"max_http_requests":80,
        "max_input_bytes_reserved":80*24576,"max_output_tokens_reserved":80*1024}
    value["prepared_id"]=hashlib.sha256(json.dumps({key:item for key,item in value.items() if key!="prepared_id"},sort_keys=True).encode()).hexdigest()
    path=tmp_path/"plan.json";path.write_text(json.dumps(value))
    monkeypatch.setattr("scripts.run_multi_intent_live.provider_stamp",lambda:{})
    with pytest.raises(ValueError,match="explicit_new_phase_authorization"):
        validated_plan(path,None)
    assert not list(tmp_path.glob("*.sqlite"))


def test_segmented_carry_is_hash_bound_before_provider_initialization(tmp_path):
    import hashlib
    from scripts.test_provenance import snapshot_sources
    root=Path(__file__).resolve().parents[1]
    value={'source_fingerprint':snapshot_sources(root)['fingerprint'],
        'dataset_sha256':hashlib.sha256(DATA.read_bytes()).hexdigest(),
        'carried_evidence':{'path':'acceptance/multi-intent/captured-preview-v1.json','sha256':'changed'}}
    value['prepared_id']=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
    path=tmp_path/'plan.json';path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='carried_evidence_changed'):
        validated_plan(path,None,rehearse=True)


def test_revalidation_rejection_closes_ledger_connection(tmp_path, monkeypatch):
    import sqlite3
    from scripts.evaluate_multi_intent import prepare
    from scripts.m5_live_budget import DispatchLedger
    dataset=json.loads(DATA.read_bytes())
    path=tmp_path/'reports/multi-intent/live-budget/original/dispatch.sqlite'
    ledger=DispatchLedger(path,'original',
        {'max_requests':80,'max_input_tokens':80*24576,'max_output_tokens':80*1024},
        operation_limits(dataset))
    ledger.reserve('MI-01/understanding',json.dumps({'max_tokens':500,'messages':[]}).encode())
    original=tmp_path/'prior.json'
    original.write_text(json.dumps({'prepared_id':'original'}),encoding='utf-8')
    connections=[]
    connect=sqlite3.connect
    def capture(*args,**kwargs):
        db=connect(*args,**kwargs)
        connections.append(db)
        return db
    monkeypatch.setattr('scripts.evaluate_multi_intent.ROOT',tmp_path)
    monkeypatch.setattr('scripts.test_provenance.snapshot_sources',lambda root:{'fingerprint':'frozen'})
    monkeypatch.setattr(sqlite3,'connect',capture)
    with pytest.raises(ValueError,match='unknown_dispatch_requires_adjudication'):
        prepare(tmp_path/'unapproved',dataset,provider={},revalidate=original)
    assert connections
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError,match='closed'):
            db.execute('SELECT 1')
