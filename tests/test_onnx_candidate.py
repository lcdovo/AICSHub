"""GPU候选来源、资源边界、数值比较与失败分母的离线保护。"""
import hashlib
import json

import pytest

from scripts import build_onnx_candidate as build
from scripts.onnx_candidate.compare_model import audit_profile, compare_scores, summarize
from scripts.onnx_candidate.export_model import check_memory
from scripts.verify_onnx_candidate import check_export, container_command, read_build


@pytest.mark.parametrize("bad", ["filename", "digest", "missing"])
def test_cached_wheel_rejects_unverified_payload(tmp_path, bad):
    path = tmp_path / ("other.whl" if bad == "filename" else build.WHEEL_NAME)
    if bad != "missing":
        path.write_bytes(b"not-a-package")
    with pytest.raises(ValueError):
        build.verify_wheel(path)


@pytest.mark.parametrize("changed", ["status", "base", "image", "drift", "incomplete"])
def test_candidate_build_must_be_pinned_and_stable(tmp_path, changed):
    from scripts.preflight_onnx_runtime import IMAGE
    data = dict(status="BUILT_NOT_INFERENCE_VERIFIED", base_image_id=IMAGE,
                candidate_image_id="sha256:" + "a" * 64, source_tree_stable=True,
                source_snapshot_complete=True)
    if changed == "status": data["status"] = "BUILD_TIMEOUT"
    elif changed == "base": data["base_image_id"] = "other"
    elif changed == "image": data["candidate_image_id"] = "latest"
    elif changed == "drift": data["source_tree_stable"] = False
    else: data["source_snapshot_complete"] = False
    path = tmp_path / "build.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        read_build(path)


def test_stages_are_offline_bounded_with_only_new_output_writable(tmp_path):
    command = container_command("export", "sha256:" + "a" * 64, tmp_path, "test-container")
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--memory") + 1] == "5g"
    assert command[command.index("--memory-swap") + 1] == "5g"
    assert "--read-only" in command and "--publish" not in command
    mounts = [command[i + 1] for i, part in enumerate(command) if part == "--mount"]
    assert len(mounts) == 3
    assert all(value.endswith(",readonly") for value in mounts[:2])
    assert "target=/onnx-output" in mounts[-1]


@pytest.mark.parametrize("host,gpu", [(0, 10**10), (10**10, 0)])
def test_export_requires_host_and_gpu_headroom(host, gpu):
    with pytest.raises(RuntimeError):
        check_memory(2271071852, host, gpu)


def test_export_headroom_only_allows_attempt_not_acceptance():
    assert check_memory(2271071852, 5 * 1024**3, 6 * 1024**3) is None


@pytest.mark.parametrize("bad", ["traversal", "size", "hash", "missing_onnx"])
def test_export_integrity_rejects_changed_or_escaped_files(tmp_path, bad):
    (tmp_path / "model").mkdir()
    payload = b"candidate"
    filename = "data.bin" if bad == "missing_onnx" else "model.onnx"
    (tmp_path / "model" / filename).write_bytes(payload)
    entry = {"path": filename, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    if bad == "traversal": entry["path"] = "../../outside.onnx"
    elif bad == "size": entry["bytes"] += 1
    elif bad == "hash": entry["sha256"] = "0" * 64
    (tmp_path / "export.json").write_text(json.dumps({"status": "EXPORTED_NOT_VALIDATED",
        "dtype": "fp16", "device": "cuda", "files": [entry]}))
    with pytest.raises(ValueError):
        check_export(tmp_path)


@pytest.mark.parametrize("values", [[1.] * 19, [1.] * 19 + [float("nan")], [True] * 20])
def test_partial_and_nonfinite_logits_rejected(values):
    with pytest.raises(ValueError):
        compare_scores([1.] * 20, values)


def test_close_logits_do_not_hide_ranking_changes():
    values = [0.001, 0.002] + [-1.] * 18
    candidate = [0.003, 0.002] + [-1.] * 18
    result = compare_scores(values, candidate)
    assert result["numeric_tolerance_pass"] and not result["top1_identical"]
    assert not result["full_rank_identical"]


def test_failure_and_late_calls_remain_in_latency_denominator():
    rows = [{"case_id": "a", "status": "COMPLETE", "rerank_ms": 1.},
            {"case_id": "b", "status": "DEADLINE_EXCEEDED", "rerank_ms": 6000.}]
    result = summarize(rows, 2)
    assert result["rerank_p95_ms_all_attempts"] == 6000.
    assert result["completed"] == 1 and not result["meets_direct_model_latency_only"]
    assert not result["full_app_capacity_verified"]


def test_cpu_shape_bookkeeping_does_not_imply_cpu_model_compute():
    result = audit_profile([
        {"args": {"provider": "CUDAExecutionProvider", "op_name": "MatMul"}},
        {"args": {"provider": "CPUExecutionProvider", "op_name": "Shape",
                  "input_type_shape": [{"float16": [4, 400, 1024]}],
                  "output_type_shape": [{"int64": [3]}]}}])
    assert result["cuda_kernel_events"] == 1
    assert result["cpu_integer_metadata_events"] == 1
    assert not result["cpu_floating_model_compute_allowed"]


def test_integer_shape_to_single_float_scalar_cast_is_explicit_metadata():
    result = audit_profile([
        {"args": {"provider": "CUDAExecutionProvider", "op_name": "MatMul"}},
        {"args": {"provider": "CPUExecutionProvider", "op_name": "Cast",
                  "input_type_shape": [{"int64": [1]}],
                  "output_type_shape": [{"float16": [1]}]}}])
    assert result["cpu_shape_scalar_cast_events"] == 1
    assert not result["cpu_floating_model_compute_allowed"]


@pytest.mark.parametrize("inputs,outputs", [([{"int64": [2]}], [{"float16": [2]}]),
                                           ([{"float": [1]}], [{"float16": [1]}])])
def test_float_tensor_casts_and_float_input_remain_rejected(inputs, outputs):
    with pytest.raises(ValueError):
        audit_profile([
            {"args": {"provider": "CUDAExecutionProvider"}},
            {"args": {"provider": "CPUExecutionProvider", "op_name": "Cast",
                      "input_type_shape": inputs, "output_type_shape": outputs}}])


@pytest.mark.parametrize("bad", ["float", "large", "model_op", "no_cuda", "empty_profile"])
def test_profile_rejects_cpu_model_compute_and_missing_proof(bad):
    events = [{"args": {"provider": "CUDAExecutionProvider", "op_name": "MatMul"}},
              {"args": {"provider": "CPUExecutionProvider", "op_name": "Gather",
                        "input_type_shape": [{"int64": [3]}],
                        "output_type_shape": [{"int64": [1]}]}}]
    if bad == "float": events[1]["args"]["output_type_shape"] = [{"float16": [1]}]
    elif bad == "large": events[1]["args"]["input_type_shape"] = [{"int64": [1025]}]
    elif bad == "model_op": events[1]["args"]["op_name"] = "MatMul"
    elif bad == "no_cuda": events.pop(0)
    else: events = []
    with pytest.raises(ValueError):
        audit_profile(events)
