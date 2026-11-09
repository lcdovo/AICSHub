"""不把CPU fallback、包存在或torch GPU成功当实际ONNX GPU模型验收。"""
from copy import deepcopy

import pytest

from scripts.preflight_onnx_runtime import ROOT, IMAGE, CACHE, assess, probe_command, verified_image


def observation():
    return {"torch_cuda_available":True, "torch_cuda_matmul_passed":True,
        "ort_available_providers":["CPUExecutionProvider","CUDAExecutionProvider"],
        "modules":{"onnx":True,"optimum.exporters.onnx":True,"tensorrt":False},
        "model_snapshots":[{"model_type":"xlm-roberta",
            "architectures":["XLMRobertaForSequenceClassification"],
            "files":[{"path":"model.safetensors","bytes":2271071852}]}]}


def test_cpu_only_runtime_blocks_even_when_torch_cuda_and_exporter_work():
    data = observation()
    data["ort_available_providers"] = ["AzureExecutionProvider","CPUExecutionProvider"]
    result = assess(data)
    assert result["status"] == "BLOCKED_GPU_RUNTIME"
    assert result["blockers"] == ["ort_cuda_provider_missing"]
    assert result["pressure_status"] == "NOT_RUN"


def test_gpu_provider_is_only_precondition_never_actual_model_pass():
    result = assess(observation())
    assert result["ort_gpu_candidate_eligible"]
    assert result["status"] == "READY_FOR_ISOLATED_EXPORT_NOT_ACCEPTED"
    assert not result["export_verified"]
    assert not result["actual_onnx_model_gpu_inference_verified"]
    assert not result["whole_m5_pass"] and not result["business_switch_approved"]
    assert not result["tensorrt_runtime_available"]


@pytest.mark.parametrize("missing", ["cuda", "compute", "onnx", "exporter", "weights", "architecture"])
def test_missing_required_prerequisite_never_enters_export(missing):
    data = observation()
    if missing == "cuda": data["torch_cuda_available"] = False
    elif missing == "compute": data["torch_cuda_matmul_passed"] = False
    elif missing == "onnx": data["modules"]["onnx"] = False
    elif missing == "exporter": data["modules"]["optimum.exporters.onnx"] = False
    elif missing == "weights": data["model_snapshots"][0]["files"] = []
    else: data["model_snapshots"][0]["architectures"] = ["SomeOtherModel"]
    assert assess(data)["status"] == "BLOCKED_GPU_RUNTIME"


def test_tensorrt_module_alone_is_not_gpu_execution_provider():
    data = observation()
    data["modules"]["tensorrt"] = True
    assert not assess(data)["tensorrt_runtime_available"]
    data["ort_available_providers"].append("TensorrtExecutionProvider")
    assert assess(data)["tensorrt_runtime_available"]


def test_probe_runs_pinned_image_offline_with_readonly_weights_and_source():
    command = probe_command(IMAGE, ROOT/"scripts/runtime_onnx_probe.py")
    assert command[command.index("--network")+1] == "none"
    assert command[command.index("--pull")+1] == "never"
    assert "--read-only" in command and "HF_HUB_OFFLINE=1" in command
    mounts = [command[i+1] for i, part in enumerate(command) if part == "--mount"]
    assert len(mounts) == 2 and all(m.endswith(",readonly") for m in mounts)
    assert "--publish" not in command and "--volume" not in command


@pytest.mark.parametrize("bad", ["image", "owner", "cache"])
def test_unowned_or_changed_image_and_model_cache_are_rejected(bad):
    obj = {"Image":IMAGE,"Config":{"Labels":{"aicshub.test.owner":"gpu-model-switch"}},
        "Mounts":[{"Name":CACHE,"Destination":"/app/.cache/huggingface"}]}
    obj = deepcopy(obj)
    if bad == "image": obj["Image"] = "other-image"
    elif bad == "owner": obj["Config"]["Labels"]["aicshub.test.owner"] = "other-project"
    else: obj["Mounts"][0]["Name"] = "business-cache"
    with pytest.raises(RuntimeError): verified_image(obj)


def test_empty_observation_fails_closed():
    result = assess({})
    assert result["status"] == "BLOCKED_GPU_RUNTIME"
    assert result["pressure_status"] == "NOT_RUN"


@pytest.mark.parametrize("failure", ["timeout", "install"])
def test_build_timeout_or_install_failure_never_claims_usable_candidate(monkeypatch, tmp_path, failure):
    import json
    import subprocess
    from scripts import build_onnx_candidate as module
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[1] == "build":
            assert kwargs["timeout"] == 360
            if failure == "timeout":
                raise subprocess.TimeoutExpired(command, 360)
            return subprocess.CompletedProcess(command, 1)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr("scripts.test_provenance.snapshot_sources", lambda _: {})
    monkeypatch.setattr("scripts.test_provenance.compare_sources", lambda *_: {
        "source_tree_stable":True, "source_snapshot_complete":True})
    assert module.main() == 2
    report = json.loads(next(tmp_path.rglob("report.json")).read_bytes())
    assert report["status"] == ("BUILD_TIMEOUT" if failure == "timeout" else "FAIL_BUILD")
    assert "candidate_image_id" not in report
    assert len(calls) == 3
