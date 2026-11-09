"""冻结任务循环实测方案与旧账本；不读取密钥、不调用模型、不启用业务配置。"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "acceptance/agent-task-loop/scenarios-v1.json"
OUTPUT = ROOT / "reports/task-loop/admission-v1"
REVISION_OUTPUT = OUTPUT / "revalidation-v2"
TARGETED_OUTPUT = OUTPUT / "revalidation-v3"
NEW_OUTPUT = OUTPUT.parent / "admission-v4"
REVISION_PREFIX = "revision-v2/"
PROTECTED = (
    "reports/m4-closeout/live/authorization-ledger.json",
    "reports/m5-live/authorization.json", "reports/m5-live/dispatch.sqlite",
    "reports/m5-judge-v2-live/authorization.json", "reports/m5-judge-v2-live/dispatch.sqlite",
    "reports/model-first-admission/authorization.json",
    "reports/model-first-admission/critical-v2-authorization.json",
    "reports/model-first-admission/dispatch.sqlite",
)


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def validate_plan(plan: dict) -> None:
    cases = plan["cases"]
    if (len(cases) != 12 or len({case["case_id"] for case in cases}) != 12
            or Counter(case["category"] for case in cases) != {"mixed": 4, "diagnostic": 4, "conditional": 4}
            or sum(len(case["turns"]) for case in cases) != 20):
        raise ValueError("fixed_case_and_turn_identity_changed")
    if (plan["proposed_limits"]["max_requests"] != 120
            or plan["max_provider_requests_per_turn"] != 6
            or plan["automatic_retry"] is not False
            or plan["automatic_budget_extension"] is not False):
        raise ValueError("fixed_request_budget_changed")
    if any(plan["business_enablement"].values()):
        raise ValueError("preparation_cannot_enable_business")


def preparation() -> dict:
    from scripts.test_provenance import snapshot_sources
    from dotenv import dotenv_values

    plan = json.loads(PLAN.read_bytes())
    validate_plan(plan)
    source = snapshot_sources(ROOT)
    if source["unsupported_paths"]:
        raise ValueError("incomplete_source_snapshot")
    values = dotenv_values(ROOT / ".env")
    provider = {name: values.get(name, "") for name in (
        "LLM_API_BASE", "LLM_MODEL", "LLM_MODEL_VERSION", "LLM_THINKING_MODE", "LLM_STRUCTURED_OUTPUT_METHOD")}
    if provider["LLM_API_BASE"].rstrip("/") != "https://token.uino.com/v1" or provider["LLM_MODEL"] != "deepseek-flash":
        raise ValueError("provider_outside_frozen_task_plan")
    result = {
        "schema_version": 1, "plan_id": plan["plan_id"], "plan_sha256": sha(PLAN),
        "source_fingerprint": source["fingerprint"], "source_file_count": len(source["files"]),
        "protected_prior_hashes": {name: sha(ROOT / name) for name in PROTECTED},
        "cases": 12, "turns": 20, "proposed_limits": plan["proposed_limits"],
        "provider": provider,
        "live_authorized": False, "provider_requests": 0, "business_enabled": False,
        "independent_human_labels": False,
    }
    result["prepared_id"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def require_prepared() -> dict:
    saved = json.loads((OUTPUT / "prepared.json").read_bytes())
    if saved != preparation():
        raise ValueError("source_plan_or_prior_ledger_changed_refreeze_required")
    return saved


def revalidation_preparation(revision: str = "v2") -> dict:
    """Freeze revised inputs while preserving the original 120-request ledger identity."""
    from scripts.m5_live_budget import validate_authorization
    from scripts.test_provenance import snapshot_sources

    if revision not in {"v2", "v3"}:
        raise ValueError("unsupported_revalidation_revision")
    prefix = "revision-" + revision + "/"
    original = json.loads((OUTPUT / "prepared.json").read_bytes())
    plan = json.loads(PLAN.read_bytes())
    validate_plan(plan)
    original_authorization = json.loads((OUTPUT / "authorization.json").read_bytes())
    limits = validate_authorization(original_authorization, plan)
    if original_authorization.get("prepared_id") != original["prepared_id"] or sha(PLAN) != original["plan_sha256"]:
        raise ValueError("original_authorization_or_dataset_changed")
    if {name: sha(ROOT / name) for name in PROTECTED} != original["protected_prior_hashes"]:
        raise ValueError("protected_prior_ledgers_changed")
    with sqlite3.connect(OUTPUT / "dispatch.sqlite") as db:
        metadata = json.loads(db.execute("SELECT value FROM metadata WHERE id=1").fetchone()[0])
        rows = db.execute("SELECT * FROM dispatches WHERE operation NOT LIKE ? ORDER BY operation",
                          (prefix + "%",)).fetchall()
    if metadata != {"plan_id": original["prepared_id"], "limits": limits}:
        raise ValueError("original_budget_identity_or_limits_changed")
    parent = OUTPUT / "current" if revision == "v2" else REVISION_OUTPUT / "current"
    previous = json.loads((parent / "report.json").read_bytes())
    if len(rows) != previous["ledger"]["requests"] or any(row[5] is None or row[6] is None for row in rows):
        raise ValueError("original_dispatches_changed_or_unsettled")
    source = snapshot_sources(ROOT)
    if source["unsupported_paths"]:
        raise ValueError("incomplete_source_snapshot")
    from dotenv import dotenv_values
    values = dotenv_values(ROOT / ".env")
    if {name: values.get(name, "") for name in original["provider"]} != original["provider"]:
        raise ValueError("provider_configuration_changed")
    result = {
        "schema_version": 1, "revision": revision, "plan_id": original["plan_id"],
        "ledger_prepared_id": original["prepared_id"], "operation_prefix": prefix,
        "source_fingerprint": source["fingerprint"], "plan_sha256": sha(PLAN),
        "provider": original["provider"], "proposed_limits": limits,
        "parent_authorization_sha256": sha(OUTPUT / "authorization.json"),
        "parent_preparation_sha256": sha(OUTPUT / "prepared.json"),
        "ledger_marker_sha256": sha((OUTPUT / "dispatch.sqlite").with_suffix(".created.json")),
        "prior_dispatches_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
        "prior_requests": len(rows), "max_additional_requests": limits["max_requests"] - len(rows),
        "prior_artifact_hashes": {path.relative_to(ROOT).as_posix(): sha(path)
                                  for directory in ({OUTPUT / "current", parent})
                                  for path in sorted(directory.rglob("*.json"))},
        "protected_prior_hashes": original["protected_prior_hashes"],
        "cases": 12, "turns": 20, "business_enabled": False,
    }
    if revision == "v3":
        result.update(case_ids=["diagnostic-01"], cases=1, turns=2,
                      scope="targeted regression of repeated diagnostic search; no claim of full fresh live suite")
    result["prepared_id"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def require_revalidation_prepared(revision: str = "v2") -> dict:
    directory = TARGETED_OUTPUT if revision == "v3" else REVISION_OUTPUT
    saved = json.loads((directory / "prepared.json").read_bytes())
    if saved != revalidation_preparation(revision):
        raise ValueError("revalidation_source_plan_or_original_evidence_changed")
    return saved


def new_phase_preparation() -> dict:
    """New explicit 120-request grant; the previous 117-request ledger is immutable."""
    original = json.loads((OUTPUT / "prepared.json").read_bytes())
    value = preparation()
    if value["provider"] != original["provider"] or value["protected_prior_hashes"] != original["protected_prior_hashes"]:
        raise ValueError("provider_or_protected_previous_scope_changed")
    with sqlite3.connect(OUTPUT / "dispatch.sqlite") as db:
        count = db.execute("SELECT count(*) FROM dispatches").fetchone()[0]
    if count != 117:
        raise ValueError("previous_task_ledger_changed")
    value.update(revision="v4", plan_id="agent-task-loop-feedback-admission-v4",
        additional_request_ceiling=120, predecessor_requests=count,
        previous_task_artifact_hashes={
            path.relative_to(ROOT).as_posix(): sha(path)
            for path in sorted(OUTPUT.rglob("*")) if path.is_file()
            and (path.suffix in {".json", ".sqlite"}) and "rehearsals" not in path.parts
        })
    value.pop("prepared_id")
    value["prepared_id"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def require_new_phase_prepared() -> dict:
    saved = json.loads((NEW_OUTPUT / "prepared.json").read_bytes())
    if saved != new_phase_preparation():
        raise ValueError("new_phase_source_or_previous_task_evidence_changed")
    return saved


def main() -> int:
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--check", action="store_true")
    group.add_argument("--prepare-revalidation", action="store_true")
    group.add_argument("--check-revalidation", action="store_true")
    group.add_argument("--prepare-targeted", action="store_true")
    group.add_argument("--check-targeted", action="store_true")
    group.add_argument("--prepare-new-phase", action="store_true")
    group.add_argument("--check-new-phase", action="store_true")
    args = parser.parse_args()
    if args.check_new_phase:
        result = require_new_phase_prepared()
    elif args.prepare_new_phase:
        if (NEW_OUTPUT / "dispatch.sqlite").exists() or (NEW_OUTPUT / "authorization.json").exists():
            raise ValueError("new_phase_already_authorized_no_reset")
        result = new_phase_preparation()
        NEW_OUTPUT.mkdir(parents=True, exist_ok=True)
        (NEW_OUTPUT / "prepared.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    elif args.check_targeted:
        result = require_revalidation_prepared("v3")
    elif args.check_revalidation:
        result = require_revalidation_prepared()
    elif args.prepare_revalidation or args.prepare_targeted:
        revision = "v3" if args.prepare_targeted else "v2"
        prefix = "revision-" + revision + "/"
        directory = TARGETED_OUTPUT if args.prepare_targeted else REVISION_OUTPUT
        with sqlite3.connect(OUTPUT / "dispatch.sqlite") as db:
            dispatched = db.execute("SELECT count(*) FROM dispatches WHERE operation LIKE ?",
                                    (prefix + "%",)).fetchone()[0]
        if dispatched or (directory / "authorization.json").exists():
            raise ValueError("revalidation_already_authorized_or_dispatched_no_refreeze")
        result = revalidation_preparation(revision)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "prepared.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    elif args.check:
        result = require_prepared()
    else:
        if (OUTPUT / "dispatch.sqlite").exists() or (OUTPUT / "authorization.json").exists():
            raise ValueError("live_scope_exists_no_silent_refreeze_or_budget_reset")
        result = preparation()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        (OUTPUT / "prepared.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "PREPARED_NOT_LIVE_AUTHORIZED", "prepared_id": result["prepared_id"],
                      "cases": result["cases"], "turns": result["turns"], "provider_requests": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
