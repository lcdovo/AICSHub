"""原ONNX→部分图融合→原ONNX的有限直调对照，门槛不变，不切业务。"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def validate_comparison(groups: list[dict]) -> dict:
    from scripts.onnx_candidate.compare_model import compare_scores
    if len(groups) != 3 or [group["variant"] for group in groups] != ["original-A1", "fusion-B", "original-A2"]:
        raise ValueError("fusion_comparison_order_or_count")
    if len({group["stage_report"].get("workload_sha256") for group in groups}) != 1:
        raise ValueError("fusion_workload_changed")
    samples, latency = [], []
    for group in groups:
        stage, data = group["stage_report"], group["comparison"]
        if (stage.get("status") != "PASS_STAGE_ONLY" or not stage.get("source_tree_stable")
                or not stage.get("source_snapshot_complete") or not stage.get("original_unchanged")
                or stage.get("final_state", {}).get("Running") is not False
                or data.get("status") != "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE"):
            raise ValueError("incomplete_or_unstable_fusion_stage")
        if (len(data.get("comparisons", [])) != 12
                or not all(row.get("numeric_tolerance_pass") for row in data["comparisons"])):
            raise ValueError("fusion_numeric_validation_missing")
        parts = data.get("groups", [])
        if len(parts) != 2 or [part["concurrency"] for part in parts] != [1, 10]:
            raise ValueError("fusion_concurrency_groups_missing")
        by_id = {}
        for part in parts:
            if len(part["rows"]) != 100:
                raise ValueError("fusion_rows_missing")
            for row in part["rows"]:
                if row.get("status") != "COMPLETE" or row["case_id"] in by_id:
                    raise ValueError("fusion_incomplete_or_duplicate_sample")
                compare_scores(row["logits"], row["logits"])
                if sorted(row.get("rank", [])) != list(range(20)):
                    raise ValueError("fusion_incomplete_rank")
                if type(row.get("rerank_ms")) not in (int, float) or not math.isfinite(row["rerank_ms"]) or row["rerank_ms"] <= 0:
                    raise ValueError("fusion_invalid_latency")
                by_id[row["case_id"]] = row
        values = sorted(row["rerank_ms"] for row in parts[1]["rows"])
        latency.append(values[94])
        samples.append(by_id)
    if not samples[0].keys() == samples[1].keys() == samples[2].keys():
        raise ValueError("fusion_cases_changed")
    if len({group["stage_report"]["start_source_fingerprint"] for group in groups}) != 1:
        raise ValueError("fusion_source_versions_changed")
    pairs = []
    for left, right in [(0, 1), (1, 2), (0, 2)]:
        details = [{"case_id": key, **compare_scores(samples[left][key]["logits"], samples[right][key]["logits"])}
                   for key in samples[0]]
        pairs.append({"left": left, "right": right, "numeric_within_tolerance": sum(row["numeric_tolerance_pass"] for row in details),
                      "top1_identical": sum(row["top1_identical"] for row in details),
                      "full_rank_identical": sum(row["full_rank_identical"] for row in details),
                      "max_logit_abs_delta": max(row["max_logit_abs_delta"] for row in details)})
    return {"concurrent10_direct_model_p95_ms": latency, "pairs": pairs,
            "candidate_within_original_direct_latency_gate": latency[1] <= 2000,
            "candidate_adopted": False, "whole_m5_pass": False, "business_switch_approved": False,
            "limit": "same200 requests/12 repeated semantic clusters; direct model only, no HTTP/Embedding/app queues, human quality or sustained capacity"}


def main() -> int:
    from scripts.test_provenance import snapshot_sources, compare_sources
    from scripts.verify_onnx_candidate import check_export, read_build
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--original-export", type=Path, required=True)
    parser.add_argument("--fusion-export", type=Path, required=True)
    args = parser.parse_args()
    output = ROOT / "reports/gpu-pair" / datetime.now().strftime("onnx-fusion-aba-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    before = snapshot_sources(ROOT)
    report = {"status": "INCOMPLETE", "groups": [], "external_model_calls": 0,
              "business_changed": False, "whole_m5_pass": False}
    try:
        read_build(args.build_report)
        report["original_export"] = check_export(args.original_export.resolve())
        report["fusion_export"] = check_export(args.fusion_export.resolve())
        report["actual_fusions"] = json.loads((args.fusion_export / "fusion.json").read_bytes())
        for variant, directory in [("original-A1", args.original_export), ("fusion-B", args.fusion_export), ("original-A2", args.original_export)]:
            command = [sys.executable, str(ROOT / "scripts/verify_onnx_candidate.py"),
                       "--build-report", str(args.build_report.resolve()), "--stage", "compare",
                       "--export-dir", str(directory.resolve())]
            run = subprocess.run(command, capture_output=True, encoding="utf-8", timeout=660)
            (output / f"{variant}.stdout.log").write_text(run.stdout, encoding="utf-8")
            (output / f"{variant}.stderr.log").write_text(run.stderr, encoding="utf-8")
            markers = [json.loads(line) for line in run.stdout.splitlines() if line.startswith('{"status":')]
            if len(markers) != 1:
                raise ValueError("missing_stage_report")
            path = Path(markers[0]["report"])
            stage = json.loads(path.read_bytes())
            group = {"variant": variant, "report_path": str(path.relative_to(ROOT)), "stage_report": stage}
            if (path.parent / "comparison.json").is_file():
                group["comparison"] = json.loads((path.parent / "comparison.json").read_bytes())
            report["groups"].append(group)
            (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            if run.returncode != 0 or "comparison" not in group:
                raise ValueError("fusion_stage_failed")
            print(f"{variant} completed", flush=True)
        report["comparison"] = validate_comparison(report["groups"])
        report["status"] = "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE"
    except Exception as exc:
        report.update(status="FAIL", error_type=type(exc).__name__)
        if isinstance(exc, ValueError):
            report["error"] = str(exc)
    finally:
        after = snapshot_sources(ROOT)
        report.update(compare_sources(before, after))
        if not report["source_tree_stable"] or not report["source_snapshot_complete"]:
            report["status"] = "FAIL_SOURCE_DRIFT"
        for name, value in [("report", report), ("source-start", before), ("source-end", after)]:
            (output / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output / "report.json")}))
    return 0 if report["status"] == "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
