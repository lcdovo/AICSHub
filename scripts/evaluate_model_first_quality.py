"""用户委托助手复核的本地开发质量评测；沿用原预算，不冒称独立人工验收。"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import evaluate_model_first_admission as base

DATA = ROOT / "acceptance/model-first/quality-reviewed-v2.json"
PREPARED = base.RUN / "prepared-quality-v2.json"
DELEGATION = base.RUN / "assistant-review-delegation.json"
CRITICAL = base.RUN / "critical-v2/current/report.json"
TOPICS = ("query_order", "refund", "complaint", "technical", "promotion", "human", "general")


def load_data() -> dict:
    data = json.loads(DATA.read_bytes())
    cases = data["cases"]
    if (data.get("reviewer") != "assistant" or data.get("independent_human_review") is not False
            or data.get("formal_admission_gold") is not False
            or [c["case_id"] for c in cases] != [f"quality-{i:03d}" for i in range(1, 301)]
            or any(len(c["expected"]["topics"]) != 1 for c in cases)
            or sum("condition" in c["expected"] for c in cases) < 50
            or sum(len(c["expected"]["required_reads"]) > 1 for c in cases) < 50):
        raise ValueError("reviewed_dataset_identity_or_provenance_invalid")
    return data


def quality_authorized() -> dict:
    limits = base.authorized_limits()
    value = json.loads(DELEGATION.read_bytes())
    if (value.get("approved_by") != "user" or value.get("user_statement") != "你帮我复核我相信你"
            or value.get("budget_scope_id") != base.sha(base.SCOPE)
            or value.get("max_total_requests") != limits["max_requests"]
            or value.get("independent_human_review") is not False
            or value.get("scope") != "assistant_reviewed_local_development_only"):
        raise ValueError("assistant_review_delegation_invalid")
    return limits


def build_quality_prepared() -> dict:
    load_data()
    quality_authorized()
    result = base.prepared_plan()
    critical = json.loads(CRITICAL.read_bytes())
    if (not critical.get("critical_pass") or critical.get("evaluated_cases") != 60
            or not critical.get("source_tree_stable") or not critical.get("source_snapshot_complete")
            or not critical.get("protected_prior_stable")
            or critical.get("end_source_fingerprint") != result["source_fingerprint"]):
        raise ValueError("critical_gate_missing_or_source_changed")
    result.pop("prepared_id", None)
    result.update(phase="reviewed_quality_v2", dataset_sha256=base.sha(DATA), expected_cases=300,
                  delegation_sha256=base.sha(DELEGATION), independent_human_labels=False,
                  formal_admission_gold=False, bulk_live_allowed=True)
    for relative in ("reports/model-first-admission/current/report.json",
                     "reports/model-first-admission/authorization.json",
                     "reports/model-first-admission/critical-v2/current/report.json"):
        result["protected_prior_hashes"][relative] = base.sha(ROOT / relative)
    result["prepared_id"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def require_quality_prepared() -> dict:
    value = json.loads(PREPARED.read_bytes())
    if value != build_quality_prepared():
        raise ValueError("quality_source_data_or_delegation_drift")
    return value


def score(rows: list[dict], data: dict) -> dict:
    cases = data["cases"]
    expected = {c["case_id"]: c for c in cases}
    actual = {r["case_id"]: r for r in rows}
    if len(actual) != len(rows) or not actual.keys() <= expected.keys():
        raise ValueError("duplicate_or_unexpected_result_ids")
    n = len(cases)
    if n != 300:
        raise ValueError("quality_requires_300_cases")
    counts = {name: {"tp": 0, "fp": 0, "fn": 0} for name in TOPICS}
    valid = types = entities = conditions = multis = 0
    condition_n = multi_n = 0
    latencies = []
    unsafe = 0
    for case_id, case in expected.items():
        row = actual.get(case_id, {})
        checks = row.get("checks", {})
        accepted = row.get("schema_valid", False)
        gold = case["expected"]["topics"][0]
        prediction = row.get("predicted_topic") if accepted else None
        for topic in TOPICS:
            counts[topic]["tp"] += gold == topic and prediction == topic
            counts[topic]["fn"] += gold == topic and prediction != topic
            counts[topic]["fp"] += gold != topic and prediction == topic
        valid += bool(accepted)
        types += bool(accepted and checks.get("request_type"))
        entities += bool(accepted and checks.get("order") and checks.get("product", True))
        if "condition" in case["expected"]:
            condition_n += 1
            conditions += bool(accepted and checks.get("condition") and checks.get("execution")
                               and checks.get("topic") and checks.get("request_type")
                               and checks.get("no_write_plan"))
        if len(case["expected"]["required_reads"]) > 1:
            multi_n += 1
            multis += bool(accepted and checks.get("read_plan") and checks.get("topic")
                          and checks.get("request_type") and checks.get("no_write_plan"))
        if case["expected"].get("no_write_plan") and row and not row.get("protected", False):
            unsafe += 1
        latency = row.get("latency_ms")
        latencies.append(float(latency) if isinstance(latency, (int, float)) and math.isfinite(latency)
                         and latency >= 0 else 1e12)
    f1 = {t: 2 * c["tp"] / (2 * c["tp"] + c["fp"] + c["fn"])
          if (2 * c["tp"] + c["fp"] + c["fn"]) else 0.0 for t, c in counts.items()}
    metrics = {"macro_f1": sum(f1.values()) / len(TOPICS), "per_topic_f1": f1,
               "request_type_accuracy": types / n, "entity_accuracy": entities / n,
               "schema_validity": valid / n,
               "condition_accuracy": conditions / condition_n if condition_n else 0.0,
               "multi_read_accuracy": multis / multi_n if multi_n else 0.0,
               "condition_samples": condition_n, "multi_read_samples": multi_n,
               "p95_ms": sorted(latencies)[math.ceil(.95 * n) - 1],
               "unsafe_plans": unsafe, "received": len(rows), "denominator": n}
    gates = {"complete": len(rows) == n, "macro_f1": metrics["macro_f1"] >= .9,
             "request_type": types / n >= .95, "entity": entities / n >= .97,
             "schema": valid / n >= .99,
             "condition": condition_n >= 50 and metrics["condition_accuracy"] >= .95,
             "multi_read": multi_n >= 50 and metrics["multi_read_accuracy"] >= .95,
             "latency": metrics["p95_ms"] <= 3000, "protected": unsafe == 0}
    return {"metrics": metrics, "gates": gates, "local_quality_pass": all(gates.values()),
            "independent_human_labels": False, "whole_admission_pass": False,
            "reviewer": "assistant", "semantic_families": data["semantic_families"],
            "formal_admission_gold": False}


def main() -> int:
    from scripts.evaluate_m5_live import exclusive_run
    from scripts.test_provenance import snapshot_sources, compare_sources
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    for action in ("prepare", "dry-run", "execute"):
        actions.add_argument("--" + action, action="store_true")
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    quality_authorized()
    if args.prepare:
        with exclusive_run(base.RUN / "execution.lock"):
            with sqlite3.connect((base.RUN / "dispatch.sqlite").resolve().as_uri() + "?mode=ro", uri=True) as db:
                if db.execute("SELECT count(*) FROM dispatches WHERE operation LIKE 'quality-%'").fetchone()[0]:
                    raise ValueError("quality_dispatch_exists_no_refreeze")
            prepared = build_quality_prepared()
            base.save(PREPARED, prepared)
        print(json.dumps({"status": "PREPARED_REVIEWED_QUALITY", "prepared_id": prepared["prepared_id"]}))
        return 0
    prepared = require_quality_prepared()
    if args.worker_output:
        with exclusive_run((args.worker_output if args.dry_run else base.RUN) / "execution.lock"):
            base.save(args.worker_output / "report.json", base.worker(prepared, args.worker_output, args.dry_run))
        return 0
    output = base.RUN / "quality-v2/current" if args.execute else base.RUN / "rehearsals" / datetime.now().strftime("quality-v2-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=True)
    before = snapshot_sources(ROOT)
    code, failure = None, None
    try:
        with tempfile.TemporaryDirectory(prefix="aicshub-quality-v2-") as temporary:
            with (output / "worker.log").open("a", encoding="utf-8") as log:
                code = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                       "--execute" if args.execute else "--dry-run", "--worker-output", str(output)],
                                      cwd=temporary, stdout=log, stderr=subprocess.STDOUT, timeout=3600).returncode
    except Exception as error:
        failure = type(error).__name__
    report_path = output / "report.json"
    result = json.loads(report_path.read_bytes()) if report_path.exists() else {"status": "FAIL_NO_REPORT"}
    after = snapshot_sources(ROOT)
    protected = all(base.sha(ROOT / p) == h for p, h in prepared["protected_prior_hashes"].items())
    result.update(score(result.get("rows", []), load_data()))
    result.update(compare_sources(before, after), protected_prior_stable=protected,
                  worker_exit_code=code, prepared_id=prepared["prepared_id"], default_changed=False)
    result["status"] = ("MOCK_PROTOCOL_ONLY" if args.dry_run else "LOCAL_QUALITY_PASS") if result["local_quality_pass"] else "FAIL_LOCAL_QUALITY"
    if code != 0 or failure or not result["source_tree_stable"] or not result["source_snapshot_complete"] or not protected:
        result.update(status="FAIL_SOURCE_OR_WORKER", local_quality_pass=False, error_type=failure or "worker_source_or_prior_report")
    for name, value in (("report", result), ("source-start", before), ("source-end", after)):
        base.save(output / (name + ".json"), value)
    print(json.dumps({"status": result["status"], "report": str(report_path)}))
    return 2 if result["status"].startswith("FAIL") else 0


if __name__ == "__main__":
    raise SystemExit(main())
