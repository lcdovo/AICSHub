"""固定本地基镜像派生GPU ORT；有界构建并保留成功/失败证据。"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


WHEEL_NAME = "onnxruntime_gpu-1.19.2-cp310-cp310-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"
WHEEL_SHA256 = "a49740e079e7c5215830d30cde3df792e903df007aa0b0fd7aa797937061b27a"


def verify_wheel(path: Path) -> None:
    if path.name != WHEEL_NAME or not path.is_file() or path.is_symlink():
        raise ValueError("unexpected_gpu_runtime_wheel")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != WHEEL_SHA256:
        raise ValueError("gpu_runtime_wheel_sha256_mismatch")


def main(wheel: Path | None = None) -> int:
    from scripts.preflight_onnx_runtime import IMAGE
    from scripts.test_provenance import snapshot_sources, compare_sources
    output = ROOT/"reports/gpu-pair"/datetime.now().strftime("onnx-build-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    before = snapshot_sources(ROOT)
    report = {"status":"INCOMPLETE", "base_image_id":IMAGE, "business_changed":False,
        "external_model_calls":0,"runtime_download_from":"PyPI", "timeout_seconds":360}
    try:
        if wheel is not None:
            verify_wheel(wheel)
            context = output / "context"
            context.mkdir()
            shutil.copyfile(wheel, context / WHEEL_NAME)
            (context / "Dockerfile").write_text(
                "FROM aicshub-onnx-base:pinned-64873a491e83\n"
                f"COPY {WHEEL_NAME} /tmp/{WHEEL_NAME}\n"
                f'RUN echo "{WHEEL_SHA256}  /tmp/{WHEEL_NAME}" | sha256sum -c - '
                "&& python -m pip uninstall -y onnxruntime "
                f"&& python -m pip install --no-index --no-deps /tmp/{WHEEL_NAME} "
                f"&& rm /tmp/{WHEEL_NAME}\n"
                "ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 DO_NOT_TRACK=1\n",
                encoding="utf-8",
            )
            report.update(runtime_download_from="verified_local_wheel",
                          wheel_sha256=WHEEL_SHA256, build_network="none")
        else:
            context = ROOT / "scripts/onnx_candidate"
        subprocess.run(["docker","image","inspect",IMAGE], check=True, capture_output=True, timeout=10)
        subprocess.run(["docker","tag",IMAGE,"aicshub-onnx-base:pinned-64873a491e83"],check=True,timeout=10)
        command = ["docker","build","--pull=false","-t","aicshub-onnx-candidate:ort-1.19.2",
            *(["--network=none"] if wheel is not None else []), str(context)]
        with (output/"build.log").open("w", encoding="utf-8") as log:
            run = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, encoding="utf-8", timeout=360)
        report["exit_code"] = run.returncode
        report["status"] = "BUILT_NOT_INFERENCE_VERIFIED" if run.returncode == 0 else "FAIL_BUILD"
        if run.returncode == 0:
            report["candidate_image_id"] = subprocess.check_output(["docker","image","inspect",
                "aicshub-onnx-candidate:ort-1.19.2","--format","{{.Id}}"], encoding="utf-8", timeout=10).strip()
    except subprocess.TimeoutExpired:
        report["status"] = "BUILD_TIMEOUT"
    except Exception as exc:
        report["status"], report["error_type"] = "FAIL_BUILD", type(exc).__name__
    finally:
        after = snapshot_sources(ROOT)
        report.update(compare_sources(before,after))
        if not report["source_tree_stable"] or not report["source_snapshot_complete"]:
            report["status"] = "FAIL_SOURCE_DRIFT"
        for name,data in [("report",report),("source-start",before),("source-end",after)]:
            (output/(name+".json")).write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":report["status"],"report":str(output/"report.json")}))
    return 0 if report["status"] == "BUILT_NOT_INFERENCE_VERIFIED" else 2


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, help="官方PyPI SHA256固定的本地wheel；安装时禁网络")
    raise SystemExit(main(parser.parse_args().wheel))
