"""只读本地快照，在隔离GPU容器导出FP16候选；产物不自动用于业务。"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time

SOURCE = Path("/app/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3/snapshots/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e")
OUTPUT = Path("/onnx-output/model")


def check_memory(weight_bytes: int, available_bytes: int, gpu_free_bytes: int) -> None:
    # FP16 GPU导出仍需CPU中间副本。前置检查不能保证峰值，外层另设容器内存上限。
    reserve = 1024 ** 3
    if weight_bytes <= 0 or available_bytes < weight_bytes + reserve:
        raise RuntimeError("insufficient_export_host_memory")
    if gpu_free_bytes < weight_bytes // 2 + reserve:
        raise RuntimeError("insufficient_export_gpu_memory")


def main() -> None:
    import torch
    from optimum.exporters.onnx import main_export

    weights = SOURCE / "model.safetensors"
    if not weights.is_file() or OUTPUT.exists():
        raise RuntimeError("export_source_missing_or_destination_exists")
    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    available = int(memory["MemAvailable"].split()[0]) * 1024
    gpu_free, _ = torch.cuda.mem_get_info()
    check_memory(weights.stat().st_size, available, gpu_free)
    began = time.monotonic()
    OUTPUT.mkdir(parents=True)
    started = {"status": "EXPORT_STARTED", "device": "cuda", "dtype": "fp16",
               "host_available_bytes": available, "gpu_free_bytes": gpu_free,
               "source_revision": SOURCE.name, "pressure_status": "NOT_RUN"}
    (OUTPUT.parent / "export-start.json").write_text(json.dumps(started, indent=2), encoding="utf-8")
    main_export(str(SOURCE), output=OUTPUT, task="text-classification", device="cuda", dtype="fp16",
                opset=17, local_files_only=True, trust_remote_code=False,
                do_validation=False, no_post_process=True, batch_size=1, sequence_length=16)
    files = []
    for path in sorted(OUTPUT.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                    digest.update(block)
            files.append({"path": str(path.relative_to(OUTPUT)), "bytes": path.stat().st_size,
                          "sha256": digest.hexdigest()})
    if not any(item["path"].endswith(".onnx") and item["bytes"] > 0 for item in files):
        raise RuntimeError("export_missing_onnx_model")
    report = {**started, "status": "EXPORTED_NOT_VALIDATED", "seconds": time.monotonic() - began,
              "files": files, "gpu_inference_verified": False, "numeric_validation_executed": False}
    (OUTPUT.parent / "export.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
