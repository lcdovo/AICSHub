"""修正后的固定关键边界复验；沿用原372额度及账本，不冒称独立质量准入。"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import evaluate_model_first_admission as base

DATA = ROOT / "acceptance/model-first/critical-v2.json"
PREPARED = base.RUN / "prepared-critical-v2.json"
AMENDMENT = base.RUN / "critical-v2-authorization.json"


def phase_authorized() -> dict:
    limits = base.authorized_limits()
    value = json.loads(AMENDMENT.read_bytes())
    if (value.get("approved_by") != "user" or not value.get("user_statement")
            or value.get("budget_scope_id") != base.sha(base.SCOPE)
            or value.get("phase") != "critical_revalidation_v2"
            or value.get("max_total_requests") != limits["max_requests"]
            or value.get("allowed_new_operations") != [f"critical-{i:03d}" for i in range(1, 61)]):
        raise ValueError("critical_phase_not_authorized")
    return limits


def build_critical_prepared() -> dict:
    result = base.prepared_plan()
    data = json.loads(DATA.read_bytes())
    if [case["case_id"] for case in data["cases"]] != [f"critical-{i:03d}" for i in range(1, 61)]:
        raise ValueError("critical_case_identity_changed")
    result.pop("prepared_id", None)
    result.update(phase="critical_revalidation_v2", dataset_sha256=base.sha(DATA),
                  expected_cases=60, stop_on_failed_first_six_or_unprotected_plan=True,
                  phase_authorization_sha256=base.sha(AMENDMENT),
                  independent_human_labels=False, whole_admission_pass=False,
                  quality_draft_not_eligible_for_dispatch=True)
    for relative in ["reports/model-first-admission/current/report.json",
                     "reports/model-first-admission/authorization.json"]:
        result["protected_prior_hashes"][relative] = base.sha(ROOT / relative)
    result["prepared_id"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def require_critical_prepared() -> dict:
    value = json.loads(PREPARED.read_bytes())
    if value != build_critical_prepared():
        raise ValueError("critical_source_data_or_authorization_drift")
    return value


def main() -> int:
    from scripts.evaluate_m5_live import exclusive_run
    from scripts.test_provenance import snapshot_sources, compare_sources

    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--prepare", action="store_true")
    actions.add_argument("--dry-run", action="store_true")
    actions.add_argument("--execute", action="store_true")
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.execute or args.prepare:
        phase_authorized()  # 在凭据/新派发前核对本轮用户授权及旧总额。
    if args.prepare:
        with exclusive_run(base.RUN / "execution.lock"):
            ledger = base.RUN / "dispatch.sqlite"
            if ledger.exists():
                with sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True) as db:
                    if db.execute("SELECT count(*) FROM dispatches WHERE operation LIKE 'critical-%'").fetchone()[0]:
                        raise ValueError("critical_dispatch_exists_no_refreeze")
            value = build_critical_prepared()
            base.save(PREPARED, value)
        print(json.dumps({"status": "PREPARED_CRITICAL_ONLY", "prepared_id": value["prepared_id"]}))
        return 0
    prepared = require_critical_prepared()
    if args.worker_output:
        with exclusive_run((args.worker_output if args.dry_run else base.RUN) / "execution.lock"):
            result = base.worker(prepared, args.worker_output, args.dry_run)
            base.save(args.worker_output / "report.json", result)
        return 0
    output = base.RUN / "critical-v2/current" if args.execute else base.RUN / "rehearsals" / datetime.now().strftime("critical-v2-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=True)
    before = snapshot_sources(ROOT)
    code, failure = None, None
    try:
        with tempfile.TemporaryDirectory(prefix="aicshub-critical-v2-") as temporary:
            with (output / "worker.log").open("a", encoding="utf-8") as log:
                code = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                       "--execute" if args.execute else "--dry-run", "--worker-output", str(output)],
                                      cwd=temporary, stdout=log, stderr=subprocess.STDOUT, timeout=900).returncode
    except Exception as error:
        failure = type(error).__name__
    result = json.loads((output / "report.json").read_bytes()) if (output / "report.json").exists() else {"status": "FAIL_NO_REPORT"}
    after = snapshot_sources(ROOT)
    protected = all(base.sha(ROOT / path) == expected for path, expected in prepared["protected_prior_hashes"].items())
    result.update(compare_sources(before, after), protected_prior_stable=protected, worker_exit_code=code,
                  prepared_id=prepared["prepared_id"], whole_admission_pass=False, default_changed=False)
    if code != 0 or failure or not result["source_tree_stable"] or not result["source_snapshot_complete"] or not protected:
        result.update(status="FAIL_SOURCE_OR_WORKER", error_type=failure or "worker_source_or_prior_report")
    for name, value in (("report", result), ("source-start", before), ("source-end", after)):
        base.save(output / (name + ".json"), value)
    print(json.dumps({"status": result["status"], "report": str(output / "report.json")}))
    return 2 if result["status"].startswith("FAIL") else 0


if __name__ == "__main__":
    raise SystemExit(main())
