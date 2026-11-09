"""现有固定镜像的断网只读ONNX/GPU预检；缺依赖时不导出、不下载、不压测。"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IMAGE = "sha256:64873a491e839963036d1171122695e3eb5780addb931a6b1df01dbf7bee921d"
OWNER = "aicshub-gpu-v2m3-candidate"
CACHE = "aicustomer_reranker_hf_cache"


def assess(observation: dict) -> dict:
    """provider可用是必要而非充分条件；不把GPU预检当实际模型验收。"""
    modules = observation.get("modules", {})
    providers = observation.get("ort_available_providers", [])
    reasons = []
    if not (observation.get("torch_cuda_available") and observation.get("torch_cuda_matmul_passed")):
        reasons.append("gpu_torch_runtime_unverified")
    if "CUDAExecutionProvider" not in providers:
        reasons.append("ort_cuda_provider_missing")
    if not modules.get("onnx") or not modules.get("optimum.exporters.onnx"):
        reasons.append("onnx_export_dependencies_missing")
    snapshots = observation.get("model_snapshots", [])
    if not any(s.get("model_type") == "xlm-roberta"
               and "XLMRobertaForSequenceClassification" in (s.get("architectures") or [])
               and any(f["path"] == "model.safetensors" and f["bytes"] > 0 for f in s.get("files", []))
               for s in snapshots):
        reasons.append("local_sequence_classifier_weights_missing")
    return {"status":"BLOCKED_GPU_RUNTIME" if reasons else "READY_FOR_ISOLATED_EXPORT_NOT_ACCEPTED",
        "blockers":reasons,
        "tensorrt_runtime_available":bool(modules.get("tensorrt") and "TensorrtExecutionProvider" in providers),
        "ort_gpu_candidate_eligible":not reasons,
        "export_verified":False, "actual_onnx_model_gpu_inference_verified":False,
        "pressure_status":"NOT_RUN", "whole_m5_pass":False, "business_switch_approved":False}


def verified_image(obj: dict) -> str:
    labels = obj.get("Config", {}).get("Labels") or {}
    mounts = obj.get("Mounts", [])
    if (obj.get("Image") != IMAGE or labels.get("aicshub.test.owner") != "gpu-model-switch"
            or not any(m.get("Name") == CACHE and m.get("Destination") == "/app/.cache/huggingface" for m in mounts)):
        raise RuntimeError("unverified_onnx_probe_resource")
    return IMAGE


def probe_command(image: str, probe: Path) -> list[str]:
    if image != IMAGE or probe.resolve() != (ROOT/"scripts/runtime_onnx_probe.py").resolve():
        raise ValueError("unverified_probe_image_or_source")
    return ["docker", "run", "--rm", "--pull", "never", "--network", "none", "--gpus", "all",
        "--read-only", "--tmpfs", "/tmp:rw,size=256m", "--label", "aicshub.test.owner=onnx-preflight",
        "--env", "HF_HUB_OFFLINE=1", "--env", "TRANSFORMERS_OFFLINE=1", "--env", "DO_NOT_TRACK=1",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--mount", f"type=volume,source={CACHE},target=/app/.cache/huggingface,readonly",
        "--mount", f"type=bind,source={probe.resolve()},target=/runtime_onnx_probe.py,readonly",
        "--entrypoint", "python", image, "/runtime_onnx_probe.py"]


def main() -> int:
    from scripts.test_provenance import snapshot_sources, compare_sources
    output = ROOT/"reports/gpu-pair"/datetime.now().strftime("onnx-preflight-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    before = snapshot_sources(ROOT)
    report = {"status":"INCOMPLETE", "business_changed":False, "external_calls":0,
        "model_calls":0,"weights_modified":False,"whole_m5_pass":False}
    try:
        obj = json.loads(subprocess.check_output(["docker","inspect",OWNER], encoding="utf-8"))[0]
        image = verified_image(obj)
        report["original_state"] = obj["State"]["Status"]
        report["image_id"] = image
        run = subprocess.run(probe_command(image, ROOT/"scripts/runtime_onnx_probe.py"),
            capture_output=True, encoding="utf-8", timeout=90)
        report["probe_exit_code"] = run.returncode
        lines = [line for line in run.stdout.splitlines() if line.startswith("AICSHUB_ONNX_PREFLIGHT=")]
        if run.returncode != 0 or len(lines) != 1:
            raise RuntimeError("onnx_probe_failed_or_missing_report")
        observation = json.loads(lines[0].split("=",1)[1])
        report["observation"] = observation
        report.update(assess(observation))
        after_state = json.loads(subprocess.check_output(["docker","inspect",OWNER], encoding="utf-8"))[0]
        report["original_state_unchanged"] = (after_state["Id"] == obj["Id"]
            and after_state["State"]["Status"] == report["original_state"])
        if not report["original_state_unchanged"]:
            raise RuntimeError("original_state_changed")
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
    return 0 if report["status"] == "READY_FOR_ISOLATED_EXPORT_NOT_ACCEPTED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
