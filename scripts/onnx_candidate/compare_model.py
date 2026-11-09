"""断网比较固定FP16模型与ONNX；只做模型直调诊断，不冒称完整应用容量。"""
from __future__ import annotations

import concurrent.futures
import json
import math
from pathlib import Path
import time


def compare_scores(reference: list[float], candidate: list[float]) -> dict:
    if (len(reference) != 20 or len(candidate) != 20
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in reference + candidate)):
        raise ValueError("incomplete_or_nonfinite_comparison")
    rank = lambda values: sorted(range(20), key=lambda i: (-values[i], i))
    left, right = rank(reference), rank(candidate)
    # 冻结数值容差只用于实现对照，不把它当人工质量标签。
    close = all(abs(a - b) <= 0.02 + 0.01 * abs(a) for a, b in zip(reference, candidate))
    return {"numeric_tolerance_pass": close, "atol": 0.02, "rtol": 0.01,
            "max_logit_abs_delta": max(abs(a - b) for a, b in zip(reference, candidate)),
            "reference_rank": left, "candidate_rank": right,
            "top1_identical": left[0] == right[0], "full_rank_identical": left == right,
            "reference_logits": reference, "candidate_logits": candidate}


def summarize(rows: list[dict], expected: int) -> dict:
    complete = (len(rows) == expected and len({row["case_id"] for row in rows}) == expected
                and all(row["status"] == "COMPLETE" for row in rows))
    values = sorted(row["rerank_ms"] for row in rows)
    p95 = values[math.ceil(len(values) * .95) - 1] if values else None
    return {"observed": len(rows), "expected": expected,
            "completed": sum(row["status"] == "COMPLETE" for row in rows),
            "rerank_p95_ms_all_attempts": p95,
            "meets_direct_model_latency_only": bool(complete and p95 <= 2000),
            "full_app_capacity_verified": False}


def audit_profile(events: list[dict]) -> dict:
    """仅允许CPU上有界整数形状运算/单标量转换；模型张量计算必须在CUDA。"""
    metadata_ops = {"Shape", "Size", "Gather", "Unsqueeze", "Squeeze", "Concat", "Slice",
                    "Cast", "Add", "Sub", "Mul", "Div", "Equal", "Where", "Reshape"}
    cpu, cuda, rejected, scalar_casts = [], 0, [], 0
    for event in events:
        args = event.get("args", {})
        provider = args.get("provider")
        if provider == "CUDAExecutionProvider":
            cuda += 1
        elif provider == "CPUExecutionProvider":
            op = args.get("op_name")
            inputs, outputs = args.get("input_type_shape", []), args.get("output_type_shape", [])
            scalar_cast = (op == "Cast" and len(inputs) == len(outputs) == 1
                           and len(inputs[0]) == len(outputs[0]) == 1
                           and all(dtype in {"int64", "int32"} and shape in ([], [1])
                                   for dtype, shape in inputs[0].items())
                           and all(dtype in {"float16", "float", "float32"} and shape in ([], [1])
                                   for dtype, shape in outputs[0].items()))
            if scalar_cast:
                scalar_casts += 1
                cpu.append({"name": event.get("name"), "op": op, "scalar_shape_cast": True})
                continue
            tensors = args.get("output_type_shape", [])
            if op not in {"Shape", "Size"}:
                tensors = [*args.get("input_type_shape", []), *tensors]
            valid = bool(tensors) and op in metadata_ops
            for tensor in tensors:
                for dtype, shape in tensor.items():
                    valid = valid and dtype in {"int64", "int32", "bool"}
                    valid = valid and all(type(dim) is int and dim >= 0 for dim in shape)
                    valid = valid and math.prod(shape) <= 1024
            (cpu if valid else rejected).append({"name": event.get("name"), "op": op,
                                                 "output_type_shape": args.get("output_type_shape")})
        elif provider:
            rejected.append({"provider": provider})
    if not cuda or rejected:
        raise ValueError("gpu_compute_or_cpu_metadata_audit_failed:" + json.dumps(rejected[:3]))
    return {"cuda_kernel_events": cuda, "cpu_metadata_events": len(cpu),
            "cpu_integer_metadata_events": len(cpu) - scalar_casts,
            "cpu_shape_scalar_cast_events": scalar_casts,
            "cpu_metadata_ops": sorted({row["op"] for row in cpu}),
            "cpu_floating_model_compute_allowed": False,
            "limit": "profiled 12 development queries; not proof of arbitrary dynamic input execution"}


def main() -> None:
    import numpy as np
    import torch
    import onnxruntime as ort
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    source = "/app/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3/snapshots/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
    output = Path("/onnx-output")
    workload = json.loads(Path("/workload.json").read_bytes())
    if len(workload) != 200 or any(len(row["documents"]) != 20 for row in workload):
        raise ValueError("original_workload_required")
    tokenizer = AutoTokenizer.from_pretrained(source, local_files_only=True, trust_remote_code=False)
    reference = AutoModelForSequenceClassification.from_pretrained(
        source, local_files_only=True, trust_remote_code=False, torch_dtype=torch.float16,
        attn_implementation="eager").eval().to("cuda")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.enable_profiling = True
    options.profile_file_prefix = str(output / "ort-profile")
    session = ort.InferenceSession("/onnx-model/model.onnx", sess_options=options,
                                   providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    session.disable_fallback()
    if session.get_providers()[0] != "CUDAExecutionProvider":
        raise RuntimeError("model_cuda_provider_not_active")
    names = {item.name for item in session.get_inputs()}

    def inputs(row, offset):
        batch = tokenizer([row["query"]] * len(row["documents"][offset:offset + 4]),
                          row["documents"][offset:offset + 4], padding=True,
                          truncation=False, return_tensors="np")
        if batch["input_ids"].shape[1] > 512:
            raise RuntimeError("original_pair_exceeds_model_token_limit")
        return {name: np.ascontiguousarray(batch[name], dtype=np.int64) for name in names}

    comparisons = []
    for row in workload[:12]:
        left, right = [], []
        for offset in range(0, 20, 4):
            batch = inputs(row, offset)
            with torch.inference_mode():
                logits = reference(**{name: torch.as_tensor(value, device="cuda") for name, value in batch.items()}).logits
                left.extend(logits.float().cpu().reshape(-1).tolist())
            right.extend(session.run(None, batch)[0].astype(np.float32).reshape(-1).tolist())
        comparisons.append({"case_id": row["case_id"], **compare_scores(left, right)})
        (output / "numeric-partial.json").write_text(json.dumps(comparisons, indent=2), encoding="utf-8")
    profile = Path(session.end_profiling())
    events = json.loads(profile.read_bytes())
    try:
        profile_audit = audit_profile(events)
    except ValueError:
        (output / "cpu-kernel-events.json").write_text(json.dumps([
            event for event in events if event.get("args", {}).get("provider") == "CPUExecutionProvider"
        ], indent=2), encoding="utf-8")
        raise
    del events
    report = {"status": "NUMERIC_COMPARISON_ONLY", "comparisons": comparisons,
              "providers": session.get_providers(), "provider_error_fallback_disabled": True,
              "cpu_shape_and_scalar_metadata_allowed": True, "profile_audit": profile_audit,
              "profile": profile.name,
              "reference_attention": "eager", "dtype": "fp16", "business_switch_approved": False,
              "whole_m5_pass": False, "pressure_status": "NOT_RUN",
              "limit": "12 repeated semantic clusters; not independent quality, HTTP/Embedding, admission, REST/WS, multiworker or sustained capacity"}
    (output / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not all(row["numeric_tolerance_pass"] for row in comparisons):
        raise RuntimeError("onnx_numeric_tolerance_failed")
    del reference
    torch.cuda.empty_cache()

    def run_case(row):
        began = time.perf_counter()
        result = {"case_id": row["case_id"], "candidate_count": 20}
        try:
            scores = []
            for offset in range(0, 20, 4):
                scores.extend(session.run(None, inputs(row, offset))[0].astype(np.float32).reshape(-1).tolist())
            compare_scores(scores, scores)  # 完整性/有限数断言，不作为独立正确性评分。
            result["rank"] = sorted(range(20), key=lambda i: (-scores[i], i))
            result["logits"] = scores
            result["status"] = "COMPLETE"
        except Exception as exc:
            result.update(status="FAIL", error_type=type(exc).__name__)
        result["rerank_ms"] = (time.perf_counter() - began) * 1000
        if result["rerank_ms"] > 5000:
            result["status"] = "DEADLINE_EXCEEDED"
        return result

    groups = []
    for concurrency, rows in [(1, workload[:100]), (10, workload[100:])]:
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(run_case, rows))
        groups.append({"concurrency": concurrency, "rows": results, "summary": summarize(results, 100)})
        report.update(groups=groups, pressure_status="PARTIAL_DIAGNOSTIC")
        (output / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    report.update(status="DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE", pressure_status="DIRECT_MODEL_DIAGNOSTIC_COMPLETE",
                  gpu_compute_verified_for_profiled_queries=True)
    (output / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "groups": [group["summary"] for group in groups]}), flush=True)


if __name__ == "__main__":
    main()
