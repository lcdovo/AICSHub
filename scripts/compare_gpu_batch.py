"""仅替换独立候选batch4/8：只读权重缓存，结束恢复原候选；不操作业务栈。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
ORIGINAL = "aicshub-gpu-v2m3-candidate"


def docker(*args, timeout=25):
    return subprocess.check_output(["docker",*args],text=True,timeout=timeout).strip()


def batch_command(command, size):
    expected = ["v2","--model-id","BAAI/bge-reranker-v2-m3","--device","cuda","--dtype","float16",
                "--port","7997","--batch-size","4","--no-compile","--no-model-warmup"]
    if command != expected or size != 8:
        raise RuntimeError("unverified_batch_experiment_command")
    result = list(command)
    result[result.index("--batch-size")+1] = str(size)
    return result


def health(port, seconds=60):
    import httpx
    until = time.monotonic()+seconds
    with httpx.Client(timeout=2,trust_env=False) as client:
        while time.monotonic() < until:
            try:
                data = client.get(f"http://127.0.0.1:{port}/models").raise_for_status().json()
                if any(m["id"] == "BAAI/bge-reranker-v2-m3" for m in data["data"]):return True
            except (httpx.HTTPError,KeyError,ValueError):pass
            time.sleep(1)
    raise RuntimeError("candidate_health_timeout")


@contextmanager
def isolated_batch(report):
    from scripts.verify_gpu_pair import inspect_candidate
    verified = inspect_candidate(ORIGINAL,7999,"7997/tcp")
    obj = json.loads(docker("inspect",ORIGINAL))[0]
    command = batch_command(obj["Config"]["Cmd"],8)
    mounts = [m for m in obj["Mounts"] if m["Destination"] == "/app/.cache/huggingface"]
    if len(mounts) != 1 or mounts[0].get("Name") != "aicustomer_reranker_hf_cache":
        raise RuntimeError("unverified_model_cache")
    name = "aicshub-batch-capacity-"+uuid.uuid4().hex[:12]
    temporary = None
    stopped = False
    report["original"] = verified
    report["temporary_name"] = name
    try:
        temporary = docker("create","--name",name,"--pull","never","--gpus","all",
                           "--label","aicshub.test.owner=gpu-batch-capacity",
                           "--publish","127.0.0.1::7997",
                           "--env","OMP_NUM_THREADS=2","--env","MKL_NUM_THREADS=2",
                           "--env","HF_HUB_OFFLINE=1","--env","TRANSFORMERS_OFFLINE=1",
                           "--mount","type=volume,source=aicustomer_reranker_hf_cache,target=/app/.cache/huggingface,readonly",
                           obj["Image"],*command)
        # 再核对ID，避免名称重用后停止其他容器。
        assert json.loads(docker("inspect",ORIGINAL))[0]["Id"] == verified["id"]
        stopped = True
        docker("stop","--time","10",verified["id"])
        docker("start",temporary)
        current = json.loads(docker("inspect",temporary))[0]
        assert current["Config"]["Labels"]["aicshub.test.owner"] == "gpu-batch-capacity"
        bindings = current["NetworkSettings"]["Ports"]["7997/tcp"]
        assert len(bindings) == 1 and bindings[0]["HostIp"] == "127.0.0.1"
        port = int(bindings[0]["HostPort"])
        report["temporary"] = {"id":temporary,"image_id":current["Image"],"command":command,
                                "port":port,"cache_read_only":not next(m["RW"] for m in current["Mounts"] if m["Destination"] == "/app/.cache/huggingface")}
        health(port)
        yield name,port
    finally:
        errors = []
        if temporary:
            try:
                info = json.loads(docker("inspect",temporary))[0]
                if info["State"]["Running"]:
                    docker("stop","--time","10",temporary)
                report["temporary_final_state"] = json.loads(docker("inspect",temporary))[0]["State"]["Status"]
            except Exception as exc:errors.append("temporary_stop:"+type(exc).__name__)
        if stopped:
            try:
                docker("start",verified["id"])
                health(7999)
                after = inspect_candidate(ORIGINAL,7999,"7997/tcp")
                assert after == verified
                report["original_restored_healthy"] = True
            except Exception as exc:errors.append("original_restore:"+type(exc).__name__)
        if errors:
            report["cleanup_errors"] = errors
            raise RuntimeError("batch_experiment_cleanup_failed")


def main():
    from scripts.test_provenance import snapshot_sources,compare_sources
    output = ROOT/"reports/gpu-pair"/datetime.now().strftime("batch-capacity-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    before = snapshot_sources(ROOT)
    report = {"status":"INCOMPLETE","business_changed":False,"external_calls":0,"batch_size":8,
              "scope":"temporary GPU candidate fixed arrivals; no production switch"}
    try:
        with isolated_batch(report) as (name,port):
            print(f"temporary batch8 ready on loopback port {port}",flush=True)
            run = subprocess.run([sys.executable,str(ROOT/"scripts/evaluate_gpu_arrivals.py"),"--count","20",
                                  "--reranker-container",name,"--reranker-port",str(port)],capture_output=True,text=True,timeout=180,cwd=ROOT)
            print(run.stdout,flush=True)
            lines = [line for line in run.stdout.splitlines() if line.startswith('{"status":')]
            if not lines:raise RuntimeError("missing_child_report")
            child = json.loads(lines[-1])
            report["arrival_report"],report["arrival_status"] = child["report"],child["status"]
            report["child_exit_code"] = run.returncode
            report["status"] = "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE" if run.returncode == 0 else "FAIL"
    except Exception as exc:
        report["status"],report["error_type"] = "FAIL",type(exc).__name__
    finally:
        after = snapshot_sources(ROOT)
        report.update(compare_sources(before,after))
        if not report["source_tree_stable"] or not report["source_snapshot_complete"]:report["status"]="FAIL_SOURCE_DRIFT"
        for name,data in [("report",report),("source-start",before),("source-end",after)]:
            (output/(name+".json")).write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":report["status"],"report":str(output/"report.json")}))
    return 0 if report["status"] == "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE" else 2


if __name__ == "__main__":raise SystemExit(main())
