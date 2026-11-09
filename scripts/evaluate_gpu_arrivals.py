"""固定到达率、有限在途的本地候选容量诊断；不修改业务配置或放宽门禁。"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run_arrivals(cases, rate, call, *, max_inflight=10, clock=time.monotonic, sleep=time.sleep):
    """固定计划时点；负载机饱和时记录未派发，禁止隐藏executor积压。"""
    if not math.isfinite(rate) or rate <= 0 or max_inflight < 1:
        raise ValueError("invalid_arrival_budget")
    slots = threading.BoundedSemaphore(max_inflight)
    lock = threading.Lock()
    active, peak = 0, 0
    started = clock()
    rows, futures = [], []

    def execute(case, planned, submitted):
        nonlocal active, peak
        entered = clock()
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            try:
                result = call(case)
            except Exception as exc:
                result = {"case_id":case["case_id"],"status":"FAIL","error_type":type(exc).__name__}
            ended = clock()
            return {**result, "case_id":case["case_id"], "planned_s":planned-started,
                    "submit_lag_ms":(submitted-planned)*1000, "worker_lag_ms":(entered-submitted)*1000,
                    "started_s":entered-started, "ended_s":ended-started,
                    "total_ms":(ended-entered)*1000, "from_planned_ms":(ended-planned)*1000}
        finally:
            with lock:active -= 1
            slots.release()

    with ThreadPoolExecutor(max_workers=max_inflight) as pool:
        for i, case in enumerate(cases):
            planned = started+i/rate
            sleep(max(0, planned-clock()))
            submitted = clock()
            if not slots.acquire(blocking=False):
                rows.append({"case_id":case["case_id"],"status":"LOAD_GENERATOR_BUSY",
                             "error_code":"load_generator_inflight_limit","planned_s":planned-started,
                             "submit_lag_ms":(submitted-planned)*1000,"total_ms":0,
                             "from_planned_ms":(submitted-planned)*1000,"local_inference":[]})
                continue
            try:
                futures.append(pool.submit(execute,case,planned,submitted))
            except BaseException:
                slots.release()
                raise
        rows.extend(f.result() for f in futures)
    return rows, {"peak_inflight":peak,"duration_s":clock()-started,"max_inflight":max_inflight}


def arrival_summary(rows, expected, timing):
    from scripts.verify_gpu_pair import summarize, percentile
    result = summarize(rows, expected)
    result.update(timing)
    result["busy"] = sum(r["status"] == "BUSY" for r in rows)
    result["generator_rejected"] = sum(r["status"] == "LOAD_GENERATOR_BUSY" for r in rows)
    result["completed_per_second"] = result["passed"]/timing["duration_s"] if timing["duration_s"] > 0 else None
    result["submit_lag_p95_ms"] = percentile([r["submit_lag_ms"] for r in rows])
    result["from_planned_p95_ms"] = percentile([r["from_planned_ms"] for r in rows])
    result["admission_queue_p95_ms"] = percentile([e["queue_ms"] for r in rows for e in r.get("local_inference",[]) if "admission_scope" in e])
    result["embedding_dispatches"] = sum(e["dispatched"] for r in rows for e in r.get("local_inference",[]) if e["purpose"] == "embedding")
    result["busy_after_dispatch"] = sum(r["status"] == "BUSY" and any(e["dispatched"] for e in r.get("local_inference",[])) for r in rows)
    if result["generator_rejected"] or (result["submit_lag_p95_ms"] or 0) > 50:
        result["status"] = "FAIL_LOAD_GENERATOR"
    return result


def verify_reranker(name, port):
    from scripts.verify_gpu_pair import inspect_candidate
    if name == "aicshub-gpu-v2m3-candidate" and port == 7999:
        return inspect_candidate(name,port,"7997/tcp")
    obj = json.loads(subprocess.check_output(["docker","inspect",name],text=True,encoding="utf-8"))[0]
    labels = obj["Config"].get("Labels") or {}
    ports = obj["NetworkSettings"]["Ports"].get("7997/tcp") or []
    if (not name.startswith("aicshub-batch-capacity-") or labels.get("aicshub.test.owner") != "gpu-batch-capacity"
            or obj["State"]["Status"] != "running" or not obj["HostConfig"].get("DeviceRequests")
            or not any(p["HostIp"] == "127.0.0.1" and p["HostPort"] == str(port) for p in ports)):
        raise RuntimeError("unverified_batch_candidate")
    return {"name":name,"id":obj["Id"],"image_id":obj["Image"],"command":obj["Config"]["Cmd"],"port":port}


@contextmanager
def model_services(port):
    from app.services.embedding_service import APIEmbedding
    from app.services.reranker_service import RerankerService
    embedding = APIEmbedding("http://127.0.0.1:8013/v1","local-only","qwen3-embedding:0.6b",dim=1024,max_retries=0,timeout=5,batch_size=4)
    try:
        reranker = RerankerService(local_base=f"http://127.0.0.1:{port}",local_model="BAAI/bge-reranker-v2-m3")
        try:yield embedding,reranker
        finally:reranker.close()
    finally:embedding.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count",type=int,choices=range(10,41),default=20,help="每到达率样本数，有限诊断")
    parser.add_argument("--reranker-container",default="aicshub-gpu-v2m3-candidate")
    parser.add_argument("--reranker-port",type=int,default=7999)
    args = parser.parse_args()
    import httpx
    import redis
    from app.services import local_admission as admission
    from scripts.test_m1_real import verified_runtime
    from scripts.test_provenance import snapshot_sources,compare_sources
    from scripts.verify_gpu_pair import inspect_candidate,run_case_scheduled,workload
    output = ROOT/"reports/gpu-pair"/datetime.now().strftime("arrivals-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    before = snapshot_sources(ROOT)
    report = {"status":"INCOMPLETE","whole_m5_pass":False,"business_changed":False,
              "external_calls":0,"chat_calls":0,"groups":[],"rates":[1,2,3,4,6],
              "scope":"finite fixed-arrival model chain, not full app or concurrency10 acceptance"}
    stop, samples = threading.Event(), []
    def sample():
        while not stop.is_set():
            try:
                r = subprocess.run(["nvidia-smi","--query-gpu=memory.used,memory.free,utilization.gpu","--format=csv,noheader,nounits"],capture_output=True,text=True,timeout=3)
                samples.append({"time":time.time(),"raw":r.stdout.strip(),"exit_code":r.returncode})
            except subprocess.TimeoutExpired:samples.append({"time":time.time(),"error_type":"TimeoutExpired"})
            stop.wait(.5)
    sampler = threading.Thread(target=sample,daemon=True)
    client = None
    try:
        report["runtime"] = [inspect_candidate("aicshub-gpu-embedding-candidate",8013,"11434/tcp"),
                             verify_reranker(args.reranker_container,args.reranker_port)]
        _,runtime,owner = verified_runtime(ROOT,reuse_m3=True)
        report["test_owner"] = owner
        client = redis.Redis(host="127.0.0.1",port=int(runtime["ports"]["redis"]),socket_timeout=.2,socket_connect_timeout=.2)
        assert client.ping()
        coordinator = admission.RedisCoordinator(client,"aicshub:test:arrivals:{"+uuid.uuid4().hex+"}")
        with httpx.Client(timeout=3,trust_env=False) as health:
            tags = health.get("http://127.0.0.1:8013/api/tags").raise_for_status().json()
            report["embedding_digest"] = next(m["digest"] for m in tags["models"] if m["name"] == "qwen3-embedding:0.6b")
            models = health.get(f"http://127.0.0.1:{args.reranker_port}/models").raise_for_status().json()
            assert any(m["id"] == "BAAI/bge-reranker-v2-m3" for m in models["data"])
        cases = workload(args.count*len(report["rates"]))
        payload = json.dumps(cases,ensure_ascii=False,sort_keys=True).encode()
        (output/"synthetic-workload.json").write_bytes(payload)
        report["workload_sha256"] = hashlib.sha256(payload).hexdigest()
        sampler.start()
        with patch.object(admission,"get_coordinator",return_value=coordinator),model_services(args.reranker_port) as services:
            report["warmups"] = []
            for i in range(2):
                warm = {**cases[i],"case_id":f"warmup-{i}","query":"warmup-only:"+cases[i]["query"]}
                report["warmups"].append(run_case_scheduled(warm,True,services))
            if any(row["status"] != "PASS" for row in report["warmups"]):
                raise RuntimeError("warmup_failed_no_load_started")
            for i,rate in enumerate(report["rates"]):
                group = cases[i*args.count:(i+1)*args.count]
                rows,timing = run_arrivals(group,rate,lambda case:run_case_scheduled(case,True,services))
                result = {"rate_per_second":rate,"rows":rows,"summary":arrival_summary(rows,args.count,timing)}
                report["groups"].append(result)
                (output/f"rate-{rate}.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
                print(f"rate={rate} completed={result['summary']['passed']}/{args.count} busy={result['summary']['busy']} status={result['summary']['status']}",flush=True)
                if any(row["status"] == "FAIL" for row in rows):
                    report["stopped_after_model_or_lease_fault"] = True
                    break
        report["status"] = "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE" if len(report["groups"]) == len(report["rates"]) else "INCOMPLETE"
    except Exception as exc:
        report["status"],report["error_type"] = "FAIL",type(exc).__name__
    finally:
        stop.set()
        if sampler.is_alive():sampler.join(timeout=4)
        if client is not None:client.close()
        after = snapshot_sources(ROOT)
        report.update(compare_sources(before,after))
        if not report["source_tree_stable"] or not report["source_snapshot_complete"]:report["status"]="FAIL_SOURCE_DRIFT"
        report["limitations"] = [f"{args.count} per rate finite observations, not sustained production QPS or200 acceptance",
                                 "all failures/generator drops retained; >50ms P95 submit lag invalidates fixed-rate group",
                                 "no background indexing or multi-API workers, HTTP wait not GPU kernel time",
                                 "same query/version synthetic fixtures not independent quality data; no production writes"]
        (output/"source-start.json").write_text(json.dumps(before,indent=2),encoding="utf-8")
        (output/"source-end.json").write_text(json.dumps(after,indent=2),encoding="utf-8")
        (output/"memory-samples.json").write_text(json.dumps(samples,indent=2),encoding="utf-8")
        (output/"report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":report["status"],"report":str(output/"report.json")}))
    return 0 if report["status"] == "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE" else 2


if __name__ == "__main__":raise SystemExit(main())
