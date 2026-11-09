"""固定新阶段请求上限及源码/旧账本保护；不创建真实付费授权。"""
import json

import pytest

from scripts import prepare_agent_task_acceptance as admission
from scripts import evaluate_agent_task_acceptance as evaluation


def test_fixed_synthetic_plan_covers_three_task_types_and_twenty_turns():
    plan = json.loads(admission.PLAN.read_bytes())
    admission.validate_plan(plan)
    assert plan["label_source"].endswith("not_independent_gold")
    assert plan["business_enablement"]["AGENT_TASK_REFUND_PREVIEW_ENABLED"] is False


@pytest.mark.parametrize("change", ["budget", "duplicate", "turn", "enable"])
def test_plan_rejects_accidental_scope_or_budget_expansion(change):
    plan = json.loads(admission.PLAN.read_bytes())
    if change == "budget":
        plan["proposed_limits"]["max_requests"] += 1
    elif change == "duplicate":
        plan["cases"][1]["case_id"] = plan["cases"][0]["case_id"]
    elif change == "turn":
        plan["cases"][0]["turns"].append("额外重跑")
    else:
        plan["business_enablement"]["AGENT_TASK_LOOP_ENABLED"] = True
    with pytest.raises(ValueError):
        admission.validate_plan(plan)


def test_preparation_rejects_changed_source_or_prior_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(admission, "OUTPUT", tmp_path)
    prepared = {"prepared_id": "frozen", "source_fingerprint": "one", "protected_prior_hashes": {"old": "one"}}
    (tmp_path / "prepared.json").write_text(json.dumps(prepared))
    monkeypatch.setattr(admission, "preparation", lambda: dict(prepared))
    assert admission.require_prepared() == prepared
    monkeypatch.setattr(admission, "preparation", lambda: {**prepared, "source_fingerprint": "two"})
    with pytest.raises(ValueError, match="source_plan_or_prior_ledger_changed"):
        admission.require_prepared()


def test_provider_operations_bound_every_turn_to_six_http_dispatches():
    plan = json.loads(admission.PLAN.read_bytes())
    operations = evaluation.operations(plan)
    assert len(operations) == 120
    assert all(cap == 1024 for cap in operations.values())
    assert "mixed-01/turn-1/http-6" in operations
    assert "mixed-01/turn-1/http-7" not in operations
    assert "mixed-01/turn-2/http-1" not in operations


def test_nearest_rank_keeps_tail_and_does_not_drop_failed_samples():
    assert evaluation.nearest_rank(list(range(1, 21)), .95) == 19
    assert evaluation.nearest_rank([1., 2., 30000.], .95) == 30000.
    assert evaluation.nearest_rank([], .95) is None


def test_live_authorization_must_bind_the_prepared_source(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, "OUTPUT", tmp_path)
    (tmp_path / "authorization.json").write_text(json.dumps({"prepared_id": "old"}))
    with pytest.raises(ValueError, match="authorization_must_bind"):
        evaluation.authorization({"prepared_id": "new"}, {})


def test_revalidation_uses_same_ledger_cap_and_keeps_original_dispatches(tmp_path):
    import hashlib
    from scripts.m5_live_budget import DispatchLedger

    plan = json.loads(admission.PLAN.read_bytes())
    old_operations = evaluation.operations(plan)
    limits = {"max_requests": 2, "max_input_tokens": 10000, "max_output_tokens": 10000}
    path = tmp_path / "dispatch.sqlite"
    old = DispatchLedger(path, "original-preparation", limits, old_operations)
    body = b'{"max_tokens":256}'
    old.reserve("mixed-01/turn-1/http-1", body)
    marker = path.with_suffix(".created.json")
    marker_before = hashlib.sha256(marker.read_bytes()).hexdigest()
    revised = DispatchLedger(path, "original-preparation", limits,
        evaluation.ledger_operations({"operation_prefix": "revision-v2/"}, plan))
    revised.reserve("revision-v2/mixed-01/turn-1/http-1", body)
    with pytest.raises(ValueError, match="authorized_budget_exhausted"):
        revised.reserve("revision-v2/mixed-02/turn-1/http-1", body)
    assert revised.snapshot()["requests"] == 2
    assert hashlib.sha256(marker.read_bytes()).hexdigest() == marker_before
    assert old.dispatched() == {"mixed-01/turn-1/http-1", "revision-v2/mixed-01/turn-1/http-1"}


def test_targeted_revalidation_preserves_previous_keys_without_new_case_expansion():
    plan = json.loads(admission.PLAN.read_bytes())
    operations = evaluation.ledger_operations({"revision": "v3", "operation_prefix": "revision-v3/",
                                             "case_ids": ["diagnostic-01"]}, plan)
    assert "mixed-01/turn-1/http-1" in operations
    assert "revision-v2/mixed-01/turn-1/http-1" in operations
    assert "revision-v3/diagnostic-01/turn-2/http-6" in operations
    assert "revision-v3/mixed-01/turn-1/http-1" not in operations
    assert "revision-v3/diagnostic-02/turn-1/http-1" not in operations


def test_new_explicit_grant_uses_distinct_budget_without_reusing_old_ledger():
    assert evaluation.budget_directory({"revision": "v4"}) == admission.NEW_OUTPUT
    assert evaluation.budget_directory({"revision": "v3"}) == admission.OUTPUT
    assert admission.NEW_OUTPUT != admission.OUTPUT


def test_diagnostic_scoring_rejects_unsupported_normal_even_with_waiting_status():
    plan = json.loads(admission.PLAN.read_bytes())
    case = next(row for row in plan["cases"] if row["case_id"] == "diagnostic-04")
    turns = [{"http_status": 200, "reply": "电源这一步已排除，请检查连接。", "message": message,
              "applications": 0, "task_id": "same", "task_status": "waiting_input",
              "replay_no_new_calls": True, "capabilities": ["search_knowledge"]}
             for message in case["turns"]]
    result = evaluation.assess_case(case, turns, {"tickets": 0, "handoffs": 0, "applications": 0, "previews": 0})
    assert not result["passed"] and not result["checks"]["no_unobserved_diagnostic_claim"]
