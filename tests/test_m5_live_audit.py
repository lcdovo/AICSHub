from copy import deepcopy

import pytest

from scripts.audit_m5_live_results import audit_bundle


def fixture():
    plan = {"plan_id": "synthetic", "source_fingerprint": "source", "operations": {"a:generation": 512, "a:judge": 256, "b:judge": 256},
            "proposed_limits": {"max_requests": 3, "max_input_tokens": 1000, "max_output_tokens": 1024},
            "provider": {"LLM_INPUT_PRICE_PER_1K_USD": "0.002", "LLM_OUTPUT_PRICE_PER_1K_USD": "0.008"}}
    auth = {"plan_id": "synthetic", **plan["proposed_limits"]}
    dataset = {"cases": [{"case_id": "a", "kind": "answerable", "required_conditions": ["condition"]},
                         {"case_id": "b", "kind": "review_negative", "p0": True}]}
    live = {"plan_id": "synthetic", "source_tree_stable": True, "source_snapshot_complete": True,
            "worker_exit_code": 0, "start_source_fingerprint": "source", "external_model_calls": 3,
            "rows": [{"case_id": "a", "candidate_gate_accepted": True}, {"case_id": "b", "candidate_gate_accepted": True}],
            "ledger": {"requests": 3, "input_reserved_upper_bound": 900, "output_reserved": 1024,
                       "known_actual_input_tokens": 100, "known_actual_output_tokens": 10}}
    review = {"label_source": "assistant_reviewed", "reviewer": "assistant", "independent_human_review": False,
              "rows": {"a": {"output_sha256": "hash", "covered_conditions": ["condition"], "critical_claims_supported": True}}}
    return plan, auth, dataset, live, review, {"a": "hash", "b": "other"}


def test_audit_execution_success_never_promotes_assistant_or_frozen_results_to_acceptance():
    result = audit_bundle(*fixture())
    assert result["status"] == "FROZEN_QUALITY_GATE_FAIL"
    assert result["false_accept_ids"] == result["p0_false_accept_ids"] == ["b"]
    assert not result["whole_m5_pass"] and not result["independent_human_review"]
    assert result["formal_per_claim_support"] is None and not result["invoice_verified"]


@pytest.mark.parametrize("corruption", ["plan", "source", "duplicate", "missing", "review_hash", "condition", "human", "budget", "layer"])
def test_audit_rejects_provenance_coverage_or_budget_corruption(corruption):
    plan, auth, dataset, live, review, hashes = deepcopy(fixture())
    if corruption == "plan": auth["plan_id"] = "other"
    elif corruption == "source": live["source_tree_stable"] = False
    elif corruption == "duplicate": live["rows"].append(live["rows"][0])
    elif corruption == "missing": live["rows"].pop()
    elif corruption == "review_hash": hashes["a"] = "modified"
    elif corruption == "condition": review["rows"]["a"]["covered_conditions"].append("unknown")
    elif corruption == "human": review["independent_human_review"] = True
    elif corruption == "budget": live["ledger"]["requests"] = 4
    else: live["external_model_calls"] = 0
    with pytest.raises(ValueError):
        audit_bundle(plan, auth, dataset, live, review, hashes)
