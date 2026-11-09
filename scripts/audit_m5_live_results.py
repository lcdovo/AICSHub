"""只读核对M5已付费结果/输出复核/固定账本；不会构造供应商客户端或重跑请求。"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


def audit_bundle(plan: dict, authorization: dict, dataset: dict, live: dict, review: dict, case_hashes: dict) -> dict:
    if authorization["plan_id"] != plan["plan_id"] or live["plan_id"] != plan["plan_id"]:
        raise ValueError("authorization_or_result_plan_mismatch")
    if (not live.get("source_tree_stable") or not live.get("source_snapshot_complete")
            or live.get("worker_exit_code") != 0 or live.get("start_source_fingerprint") != plan["source_fingerprint"]):
        raise ValueError("live_source_provenance_missing")
    by_case = {case["case_id"]: case for case in dataset["cases"]}
    by_output = {row["case_id"]: row for row in live["rows"]}
    if len(by_output) != len(live["rows"]) or set(by_case) != set(by_output):
        raise ValueError("missing_duplicate_or_unknown_live_case")
    generated = {key for key, case in by_case.items() if case["kind"] in {"answerable", "unanswerable"}}
    if (review.get("label_source") != "assistant_reviewed" or review.get("reviewer") != "assistant"
            or set(review["rows"]) != generated or review.get("independent_human_review") is not False):
        raise ValueError("output_review_scope_or_provenance_invalid")
    for key, item in review["rows"].items():
        if item["output_sha256"] != case_hashes[key]:
            raise ValueError("reviewed_output_changed")
        if not set(item["covered_conditions"]) <= set(by_case[key].get("required_conditions", [])):
            raise ValueError("unknown_condition_annotation")
    used = live["ledger"]
    for field, used_field in (("max_requests", "requests"), ("max_input_tokens", "input_reserved_upper_bound"),
                              ("max_output_tokens", "output_reserved")):
        if used[used_field] > authorization[field] or authorization[field] > plan["proposed_limits"][field]:
            raise ValueError("authorization_limit_exceeded")
    if used["requests"] != len(plan["operations"]) or live["external_model_calls"] != used["requests"]:
        raise ValueError("incomplete_dispatch_or_wrong_layer")
    negative = [row for key, row in by_output.items() if by_case[key]["kind"] == "review_negative"]
    positive = [row for key, row in by_output.items() if by_case[key]["kind"] == "review_positive"]
    false_accept = [row["case_id"] for row in negative if row["candidate_gate_accepted"]]
    false_reject = [row["case_id"] for row in positive if not row["candidate_gate_accepted"]]
    p0 = [key for key in false_accept if by_case[key].get("p0")]
    counts = {}
    for kind in ("answerable", "unanswerable"):
        group = [(key, row, review["rows"][key]) for key, row in by_output.items() if by_case[key]["kind"] == kind]
        counts[kind] = {"count": len(group), "gate_accepted": sum(row["candidate_gate_accepted"] for _, row, _ in group),
            "assistant_supported_accepted": sum(note["critical_claims_supported"] and row["candidate_gate_accepted"] for _, row, note in group),
            "assistant_supported_rejected": sum(note["critical_claims_supported"] and not row["candidate_gate_accepted"] for _, row, note in group),
            "assistant_flagged_accepted": sum(not note["critical_claims_supported"] and row["candidate_gate_accepted"] for _, row, note in group),
            "assistant_flagged_rejected": sum(not note["critical_claims_supported"] and not row["candidate_gate_accepted"] for _, row, note in group)}
    input_rate = Decimal(plan["provider"]["LLM_INPUT_PRICE_PER_1K_USD"])
    output_rate = Decimal(plan["provider"]["LLM_OUTPUT_PRICE_PER_1K_USD"])
    false_accept_rate = len(false_accept) / len(negative) if negative else None
    false_reject_rate = len(false_reject) / len(positive) if positive else None
    failed = bool(p0 or (false_accept_rate is not None and false_accept_rate > .02)
                  or (false_reject_rate is not None and false_reject_rate > .05))
    return {"status": "FROZEN_QUALITY_GATE_FAIL" if failed else "INCOMPLETE_NOT_ACCEPTANCE",
        "whole_m5_pass": False, "label_source": "assistant_reviewed", "independent_human_review": False,
        "source_fingerprint": plan["source_fingerprint"], "external_model_calls": used["requests"],
        "generated_outputs_reviewed": len(generated), "fixed_candidates_scored": len(negative) + len(positive),
        "negative_count": len(negative), "false_accept_ids": sorted(false_accept),
        "false_accept_rate": false_accept_rate,
        "positive_count": len(positive), "false_reject_ids": sorted(false_reject),
        "false_reject_rate": false_reject_rate,
        "p0_false_accept_ids": p0, "generated_draft_screening": counts,
        "estimated_cost_usd_unverified_rates": str((Decimal(used["known_actual_input_tokens"]) * input_rate
            + Decimal(used["known_actual_output_tokens"]) * output_rate) / 1000),
        "invoice_verified": False, "formal_per_claim_support": None,
        "limits": ["fixed labels remain frozen including disputed labels, not independent unseen holdout",
                   "assistant flags are conservative per-draft screening, not human per-claim truth",
                   "candidate validation is not a complete REST/WS publication or deployment test"]}


def main() -> int:
    directory = ROOT / "reports/m5-live"
    current = directory / "current"
    plan = json.loads((ROOT / "docs/M5-LIVE-PLAN-v1.json").read_bytes())
    authorization = json.loads((directory / "authorization.json").read_bytes())
    dataset = json.loads((ROOT / "acceptance/m5/adjudicated-v2/dataset.json").read_bytes())
    live = json.loads((current / "report.json").read_bytes())
    review = json.loads((current / "assistant-output-review.json").read_bytes())
    hashes = {}
    for row in live["rows"]:
        path = current / "cases" / (hashlib.sha256(row["case_id"].encode()).hexdigest() + ".json")
        raw = path.read_bytes()
        if json.loads(raw) != row:
            raise ValueError("persisted_case_differs_from_frozen_report")
        hashes[row["case_id"]] = hashlib.sha256(raw).hexdigest()
    for relative, expected in plan["input_hashes"].items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("frozen_input_material_changed")
    if hashlib.sha256((ROOT / "reports/m4-closeout/live/authorization-ledger.json").read_bytes()).hexdigest() != plan["old_m4_ledger_sha256"]:
        raise ValueError("old_m4_ledger_changed")
    result = audit_bundle(plan, authorization, dataset, live, review, hashes)
    with closing(sqlite3.connect("file:" + (directory / "dispatch.sqlite").resolve().as_posix() + "?mode=ro", uri=True)) as db:
        dispatched = db.execute("SELECT operation,status,http_status,input_actual,output_actual FROM dispatches").fetchall()
    if set(row[0] for row in dispatched) != set(plan["operations"]) or any(row[1:3] != ("http_complete", 200) for row in dispatched):
        raise ValueError("incomplete_or_failed_http_dispatch")
    if (sum(row[3] for row in dispatched) != live["ledger"]["known_actual_input_tokens"]
            or sum(row[4] for row in dispatched) != live["ledger"]["known_actual_output_tokens"]):
        raise ValueError("provider_usage_ledger_mismatch")
    result["http_status_counts"] = dict(Counter(str(row[2]) for row in dispatched))
    result["old_m4_ledger_unchanged"] = True
    result["evidence_hashes_verified"] = True
    path = current / "closeout-audit.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "report": str(path), "requests": len(dispatched)}))
    return 0  # 审计执行成功与质量门槛通过分别记录。


if __name__ == "__main__":
    raise SystemExit(main())
