"""同一单进程微批候选的batch4/8/4有限压力对照；自动恢复原模型服务。"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run_pressure(name: str, port: int) -> dict:
    result = subprocess.run([sys.executable, str(ROOT/"scripts/verify_gpu_pair.py"),
        "--count", "200", "--skip-compatibility", "--scheduled", "--shared-services",
        "--whole-request", "--coordinator", "batch", "--reranker-container", name,
        "--reranker-port", str(port)], cwd=ROOT, capture_output=True, text=True, timeout=240)
    lines = [line for line in result.stdout.splitlines() if line.startswith('{"status":')]
    if not lines:
        raise RuntimeError("pressure_report_missing")
    pointer = Path(json.loads(lines[-1])["report"]).resolve()
    if not pointer.is_relative_to((ROOT/"reports/gpu-pair").resolve()):
        raise RuntimeError("pressure_report_outside_workspace")
    report = json.loads(pointer.read_bytes())
    if result.returncode not in (0, 2) or not report.get("source_tree_stable") or not report.get("source_snapshot_complete"):
        raise RuntimeError("pressure_source_or_runner_failure")
    return {"path":str(pointer.relative_to(ROOT)), "exit_code":result.returncode, **report}


def validate_group(report: dict) -> None:
    groups = report.get("groups", [])
    if len(groups) != 2 or [g["concurrency"] for g in groups] != [1, 10]:
        raise RuntimeError("pressure_group_missing")
    for group in groups:
        rows = group["rows"]
        summary = group["summary"]
        if (len(rows) != 100 or len({r["case_id"] for r in rows}) != 100
                or summary["observed"] != 100 or summary["passed"] != 100
                or any(r["status"] != "PASS" for r in rows)):
            raise RuntimeError("pressure_failure_no_further_load")


def compare(groups: list[dict]) -> dict:
    if len(groups) != 3 or [g["variant"] for g in groups] != ["batch4-A1", "batch8-B", "batch4-A2"]:
        raise ValueError("incomplete_batch_comparison")
    if len({g["workload_sha256"] for g in groups}) != 1:
        raise ValueError("workload_mismatch")
    for group in groups:
        validate_group(group)
    latency = [g["groups"][1]["summary"]["rerank_p95_ms_all_attempts"] for g in groups]
    if any(type(value) not in (int, float) or not 0 < value < float("inf") for value in latency):
        raise ValueError("invalid_comparison_latency")
    return {"concurrent10_rerank_p95_ms":latency,
        "batch8_meets_original_latency_gate":latency[1] <= 2000,
        "batch8_faster_than_both_controls":latency[1] < min(latency[0], latency[2]),
        "whole_m5_pass":False, "business_switch_approved":False}


def main() -> int:
    from scripts.compare_gpu_batch import isolated_batch, health, ORIGINAL
    from scripts.test_provenance import snapshot_sources, compare_sources
    output = ROOT/"reports/gpu-pair"/datetime.now().strftime("batch-pressure-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    before = snapshot_sources(ROOT)
    report = {"status":"INCOMPLETE", "groups":[], "external_calls":0,
        "business_changed":False, "whole_m5_pass":False,
        "scope":"600 finite model chains on unchanged workload, not independent quality or full app"}
    try:
        health(7999)
        first = run_pressure(ORIGINAL, 7999)
        report["groups"].append({**first, "variant":"batch4-A1"})
        validate_group(first)
        print("batch4-A1 complete", flush=True)
        with isolated_batch(report) as (name, port):
            middle = run_pressure(name, port)
            report["groups"].append({**middle, "variant":"batch8-B"})
            validate_group(middle)
            print("batch8-B complete", flush=True)
        last = run_pressure(ORIGINAL, 7999)
        report["groups"].append({**last, "variant":"batch4-A2"})
        report["comparison"] = compare(report["groups"])
        report["status"] = "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE"
    except Exception as exc:
        report["status"], report["error_type"] = "FAIL", type(exc).__name__
    finally:
        after = snapshot_sources(ROOT)
        report.update(compare_sources(before, after))
        if not report["source_tree_stable"] or not report["source_snapshot_complete"]:
            report["status"] = "FAIL_SOURCE_DRIFT"
        for name, data in [("report",report), ("source-start",before), ("source-end",after)]:
            (output/(name+".json")).write_text(json.dumps(data, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"status":report["status"],"report":str(output/"report.json")}))
    return 0 if report["status"] == "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
