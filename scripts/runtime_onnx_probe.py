"""只读/断网容器内运行：检查导出依赖与GPU执行器，不导出或改写权重。"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import pathlib
import shutil


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def main() -> None:
    import onnxruntime as ort
    import torch

    report = {"status":"OBSERVED", "network":"none", "weights_modified":False,
        "export_executed":False, "model_inference_executed":False}
    report["versions"] = {}
    for name in ("torch", "transformers", "onnxruntime", "onnxruntime-gpu", "onnx",
                 "optimum", "tensorrt", "nvidia-cudnn-cu12", "nvidia-cuda-runtime-cu12"):
        try:
            report["versions"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            report["versions"][name] = None
    report["modules"] = {name:module_available(name) for name in (
        "onnx", "optimum.onnxruntime", "optimum.exporters.onnx", "tensorrt")}
    report["ort_available_providers"] = ort.get_available_providers()
    report["ort_provider_libraries"] = sorted(p.name for p in pathlib.Path(ort.__file__).parent.rglob("*.so"))
    report["torch_cuda_available"] = torch.cuda.is_available()
    report["torch_cuda_version"] = torch.version.cuda
    if report["torch_cuda_available"]:
        report["gpu_name"] = torch.cuda.get_device_name(0)
        report["gpu_compute_capability"] = list(torch.cuda.get_device_capability(0))
        # 只验证torch可用，不将torch成功推广为ONNX Runtime或TensorRT成功。
        result = torch.ones((2,2), device="cuda") @ torch.ones((2,2), device="cuda")
        torch.cuda.synchronize()
        report["torch_cuda_matmul_passed"] = bool((result == 2).all().item())
    root = pathlib.Path("/app/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3")
    report["model_snapshots"] = []
    for snapshot in sorted((root/"snapshots").glob("*")):
        if not snapshot.is_dir():
            continue
        files = []
        for file in sorted(snapshot.rglob("*")):
            if file.is_file():
                files.append({"path":str(file.relative_to(snapshot)), "bytes":file.stat().st_size})
        config = snapshot/"config.json"
        config_data = json.loads(config.read_bytes()) if config.is_file() else {}
        report["model_snapshots"].append({"revision":snapshot.name,"files":files,
            "model_type":config_data.get("model_type"),"architectures":config_data.get("architectures"),
            "config_sha256":hashlib.sha256(config.read_bytes()).hexdigest() if config.is_file() else None})
    report["cached_onnx_files"] = [str(p.relative_to(root)) for p in root.rglob("*.onnx")]
    report["cached_wheels"] = [str(p) for directory in ("/root/.cache/pip", "/app/.cache")
        for p in pathlib.Path(directory).rglob("*.whl")]
    report["container_disk_free_bytes"] = shutil.disk_usage("/").free
    # 所有可用provider也只是预检，不在此声称已载入实际导出模型。
    print("AICSHUB_ONNX_PREFLIGHT="+json.dumps(report, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
