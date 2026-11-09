"""只读权重、固定FP16/batch4的原注意力/原生注意力/恢复对照。"""
from __future__ import annotations

from datetime import datetime
import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def native_runtime(log: str) -> list[dict]:
    markers = [json.loads(line.split("AICSHUB_ATTENTION_RUNTIME=",1)[1])
        for line in log.splitlines() if "AICSHUB_ATTENTION_RUNTIME=" in line]
    if not any(m.get("attention_implementation") == "sdpa"
            and m.get("dtype") == "torch.float16"
            and "XLMRobertaSdpaSelfAttention" in m.get("attention_classes", []) for m in markers):
        raise RuntimeError("native_sdpa_not_verified")
    return markers


def compare_attention(groups: list[dict]) -> dict:
    from scripts.compare_batch_pressure import validate_group
    if len(groups) != 3 or [g["variant"] for g in groups] != ["original-A1", "native-B", "original-A2"]:
        raise ValueError("incomplete_attention_comparison")
    if len({g["workload_sha256"] for g in groups}) != 1:
        raise ValueError("attention_workload_mismatch")
    samples = []
    latency = []
    for group in groups:
        validate_group(group)
        rows = [r for part in group["groups"] for r in part["rows"]]
        by_id = {r["case_id"]:r for r in rows}
        if len(by_id) != 200:
            raise ValueError("duplicate_attention_case")
        for row in rows:
            scores = row.get("rerank_scores", {})
            if (len(scores) != 20 or set(scores) != {str(i) for i in range(20)}
                    or sorted(row.get("rank", [])) != sorted(scores)
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in scores.values())):
                raise ValueError("incomplete_attention_scores")
        samples.append(by_id)
        value = group["groups"][1]["summary"]["rerank_p95_ms_all_attempts"]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("invalid_attention_latency")
        latency.append(value)
    if not samples[0].keys() == samples[1].keys() == samples[2].keys():
        raise ValueError("attention_cases_mismatch")
    pairs = []
    for left, right in [(0,1), (1,2), (0,2)]:
        equal = sum(samples[left][k]["rank"] == samples[right][k]["rank"] for k in samples[0])
        top1 = sum(samples[left][k]["rank"][:1] == samples[right][k]["rank"][:1] for k in samples[0])
        delta = max(abs(samples[left][k]["rerank_scores"][str(i)]-samples[right][k]["rerank_scores"][str(i)])
                    for k in samples[0] for i in range(20))
        pairs.append({"left":left, "right":right, "full_rank_equal":equal,
            "top1_equal":top1, "max_rounded_score_abs_delta":delta})
    return {"concurrent10_rerank_p95_ms":latency, "pairs":pairs,
        "native_meets_original_latency_gate":latency[1] <= 2000,
        "whole_m5_pass":False, "business_switch_approved":False,
        "score_precision_note":"application rerank_score rounded to 4 decimals; not raw logits equivalence"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-batch-size", type=int, choices=[4,8], default=4)
    args = parser.parse_args()
    from scripts.compare_batch_pressure import run_pressure, validate_group
    from scripts.compare_gpu_batch import isolated_batch, health, ORIGINAL, docker
    from scripts.test_provenance import snapshot_sources, compare_sources
    output = ROOT/"reports/gpu-pair"/datetime.now().strftime("attention-pressure-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    before = snapshot_sources(ROOT)
    report = {"status":"INCOMPLETE", "groups":[], "external_calls":0,
        "native_batch_size":args.native_batch_size,
        "business_changed":False, "whole_m5_pass":False,
        "scope":"fixed weights FP16/batch4; finite local attention comparison,not full app/quality"}
    try:
        health(7999)
        first = run_pressure(ORIGINAL, 7999)
        report["groups"].append({**first, "variant":"original-A1"})
        validate_group(first)
        print("original-A1 complete", flush=True)
        with isolated_batch(report, native_attention=True, native_batch_size=args.native_batch_size) as (name, port):
            # 在请求日志增长前捕获启动标记，防止tail裁掉实现核验证据。
            log = docker("logs", "--tail", "200", name)
            report["attention_runtime"] = native_runtime(log)
            report["native_runtime_markers"] = [line for line in log.splitlines()
                if any(word in line.lower() for word in ("bettertransformer", "attention", "torch", "dtype"))]
            middle = run_pressure(name, port)
            report["groups"].append({**middle, "variant":"native-B"})
            validate_group(middle)
            print("native-B complete", flush=True)
        last = run_pressure(ORIGINAL, 7999)
        report["groups"].append({**last, "variant":"original-A2"})
        report["comparison"] = compare_attention(report["groups"])
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
    print(json.dumps({"status":report["status"], "report":str(output/"report.json")}))
    return 0 if report["status"] == "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
