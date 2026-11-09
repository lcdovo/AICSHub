"""有界运行独立ONNX候选：只读权重/脚本，结束停止本次容器并封存证据。"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OWNER = "onnx-candidate-verification"


def read_build(path: Path) -> str:
    from scripts.preflight_onnx_runtime import IMAGE
    data = json.loads(path.read_bytes())
    image = data.get("candidate_image_id", "")
    if (data.get("status") != "BUILT_NOT_INFERENCE_VERIFIED" or data.get("base_image_id") != IMAGE
            or not data.get("source_tree_stable") or not data.get("source_snapshot_complete")
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", image)):
        raise ValueError("unverified_candidate_build")
    return image


def check_export(directory: Path) -> dict:
    data = json.loads((directory / "export.json").read_bytes())
    if (data.get("status") != "EXPORTED_NOT_VALIDATED" or data.get("dtype") != "fp16"
            or data.get("device") != "cuda" or not data.get("files")):
        raise ValueError("unverified_export_manifest")
    for item in data["files"]:
        path = directory / "model" / item["path"]
        if (not path.resolve().is_relative_to((directory / "model").resolve()) or path.is_symlink()
                or not path.is_file() or path.stat().st_size != item["bytes"]):
            raise ValueError("export_file_changed")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != item["sha256"]:
            raise ValueError("export_file_changed")
    if not any(item["path"].endswith(".onnx") for item in data["files"]):
        raise ValueError("export_model_missing")
    return data


def container_command(stage: str, image: str, output: Path, name: str,
                      exported: Path | None = None) -> list[str]:
    from scripts.preflight_onnx_runtime import CACHE
    if stage not in {"smoke", "export", "compare", "optimize"}:
        raise ValueError("unknown_onnx_stage")
    script = {"smoke": "cuda_smoke.py", "export": "export_model.py", "compare": "compare_model.py",
              "optimize": "optimize_model.py"}[stage]
    command = ["docker", "create", "--name", name, "--pull", "never", "--network", "none",
               "--gpus", "all", "--read-only", "--memory", "5g", "--memory-swap", "5g", "--cpus", "4",
               "--tmpfs", "/tmp:rw,size=512m", "--label", f"aicshub.test.owner={OWNER}",
               "--env", "HF_HUB_OFFLINE=1", "--env", "TRANSFORMERS_OFFLINE=1",
               "--env", "DO_NOT_TRACK=1", "--env", "PYTHONDONTWRITEBYTECODE=1",
               "--env", "OMP_NUM_THREADS=2", "--env", "MKL_NUM_THREADS=2",
               "--env", "TRANSFORMERS_CACHE=/tmp/transformers",
               "--mount", f"type=volume,source={CACHE},target=/app/.cache/huggingface,readonly",
               "--mount", f"type=bind,source={ROOT / 'scripts/onnx_candidate' / script},target=/stage.py,readonly",
               "--mount", f"type=bind,source={output},target=/onnx-output"]
    if stage in {"compare", "optimize"}:
        if exported is None:
            raise ValueError("export_directory_required")
        command.extend(["--mount", f"type=bind,source={exported / 'model'},target=/onnx-model,readonly"])
        if stage == "compare":
            command.extend(["--mount", f"type=bind,source={output / 'workload.json'},target=/workload.json,readonly"])
    return [*command, "--entrypoint", "python", image, "/stage.py"]


def main(argv: list[str] | None = None) -> int:
    from scripts.preflight_onnx_runtime import OWNER as ORIGINAL, verified_image
    from scripts.test_provenance import snapshot_sources, compare_sources
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--stage", choices=["smoke", "export", "compare", "optimize"], required=True)
    parser.add_argument("--export-dir", type=Path)
    args = parser.parse_args(argv)
    output = ROOT / "reports/gpu-pair" / datetime.now().strftime(f"onnx-{args.stage}-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    before = snapshot_sources(ROOT)
    report = {"status": "INCOMPLETE", "stage": args.stage, "business_changed": False,
              "external_model_calls": 0, "whole_m5_pass": False, "business_switch_approved": False}
    container = None
    original = None
    try:
        image = read_build(args.build_report)
        original = json.loads(subprocess.check_output(["docker", "inspect", ORIGINAL], encoding="utf-8", timeout=10))[0]
        verified_image(original)
        report.update(candidate_image_id=image, original_id=original["Id"], original_state=original["State"]["Status"])
        exported = args.export_dir.resolve() if args.export_dir else None
        if args.stage in {"compare", "optimize"}:
            if exported is None:
                raise ValueError("export_directory_required")
            report["export"] = check_export(exported)
        if args.stage == "compare":
            from scripts.verify_gpu_pair import workload
            rows = workload(200)
            encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()
            (output / "workload.json").write_bytes(encoded)
            report["workload_sha256"] = hashlib.sha256(encoded).hexdigest()
        command = container_command(args.stage, image, output.resolve(), "aicshub-onnx-" + uuid.uuid4().hex[:12], exported)
        report["command"] = command
        container = subprocess.check_output(command, encoding="utf-8", timeout=30).strip()
        report["container_id"] = container
        subprocess.run(["docker", "start", container], check=True, capture_output=True, timeout=15)
        timeout = 600 if args.stage != "smoke" else 90
        report["timeout_seconds"] = timeout
        try:
            code = subprocess.check_output(["docker", "wait", container], encoding="utf-8", timeout=timeout)
            report["exit_code"] = int(code.strip())
            report["status"] = "PASS_STAGE_ONLY" if report["exit_code"] == 0 else "FAIL_STAGE"
        except subprocess.TimeoutExpired:
            report["status"] = "STAGE_TIMEOUT"
    except Exception as exc:
        report["status"], report["error_type"] = "FAIL", type(exc).__name__
        report["error"] = str(exc)[:180] if isinstance(exc, ValueError) else "stage_or_docker_failed"
    finally:
        if container:
            try:
                # 只操作本次docker create得到的不可变ID；先stop再inspect，避免inspect异常跳过停止。
                subprocess.run(["docker", "stop", "--time", "10", container], check=True, capture_output=True, timeout=20)
                final = json.loads(subprocess.check_output(["docker", "inspect", container], encoding="utf-8", timeout=10))[0]
                report["final_state"] = final["State"]
                if final["State"]["Running"] or final["Config"]["Labels"].get("aicshub.test.owner") != OWNER:
                    report["status"] = "FAIL_CLEANUP"
                log = subprocess.run(["docker", "logs", container], capture_output=True, encoding="utf-8", timeout=15)
                (output / "stdout.log").write_text(log.stdout, encoding="utf-8")
                (output / "stderr.log").write_text(log.stderr, encoding="utf-8")
            except Exception as exc:
                report["cleanup_error"] = type(exc).__name__
                report["status"] = "FAIL_CLEANUP"
        if original:
            try:
                current = json.loads(subprocess.check_output(["docker", "inspect", ORIGINAL], encoding="utf-8", timeout=10))[0]
                report["original_unchanged"] = current["Id"] == original["Id"] and current["State"]["Status"] == original["State"]["Status"]
                if not report["original_unchanged"]:
                    report["status"] = "FAIL_ORIGINAL_CHANGED"
            except Exception:
                report["status"] = "FAIL_ORIGINAL_CHECK"
        after = snapshot_sources(ROOT)
        report.update(compare_sources(before, after))
        if not report["source_tree_stable"] or not report["source_snapshot_complete"]:
            report["status"] = "FAIL_SOURCE_DRIFT"
        for name, value in [("report", report), ("source-start", before), ("source-end", after)]:
            (output / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output / "report.json")}))
    return 0 if report["status"] == "PASS_STAGE_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
