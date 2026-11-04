"""隔离候选上的生命周期对照与最多2路provider容量诊断；不改变业务准入。"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def timed_case(case, mode, services=None, http_client=None):
    from scripts.diagnose_local_admission import CASE
    from scripts.verify_gpu_pair import run_case
    token = CASE.set(mode+":"+case["case_id"])
    began = time.perf_counter()
    try:
        row = run_case(case, scheduled=mode != "provider", whole_request=mode != "provider",
                       services=services, http_client=http_client)
        return {**row, "started":began, "ended":time.perf_counter()}
    finally:
        CASE.reset(token)


def group_summary(rows, count, duration):
    from scripts.verify_gpu_pair import summarize, percentile
    result = summarize(rows, count)
    result["group_seconds"] = duration
    result["completed_per_second"] = result["passed"]/duration if duration > 0 else None
    result["embedding_p95_ms"] = percentile([r["embedding_ms"] for r in rows if "embedding_ms" in r])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, choices=range(2, 21), default=12,
                        help="每组请求数；仅有限诊断，不作为200次门禁")
    args = parser.parse_args()
    import httpx
    import redis
    from app.services import local_admission as admission
    from scripts.diagnose_local_admission import Recorder
    from scripts.test_m1_real import verified_runtime
    from scripts.test_provenance import snapshot_sources, compare_sources
    from scripts.verify_gpu_pair import inspect_candidate, scheduled_services, workload, percentile

    output = ROOT/"reports/gpu-pair"/datetime.now().strftime("capacity-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    before = snapshot_sources(ROOT)
    recorder = Recorder()
    report = {"status":"INCOMPLETE", "whole_m5_pass":False, "business_changed":False,
              "external_calls":0, "chat_calls":0, "groups":[],
              "scope":"paired lifecycle and bounded raw-provider diagnosis; not app capacity acceptance"}
    stop, samples = threading.Event(), []
    def sample():
        while not stop.is_set():
            try:
                raw = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.free,utilization.gpu",
                                      "--format=csv,noheader,nounits"],capture_output=True,text=True,timeout=3)
                samples.append({"time":time.time(),"raw":raw.stdout.strip(),"exit_code":raw.returncode})
            except subprocess.TimeoutExpired:
                samples.append({"time":time.time(),"error_type":"TimeoutExpired"})
            stop.wait(.5)
    sampler = threading.Thread(target=sample,daemon=True)
    client = None
    original_init, original_send, original_close = httpx.Client.__init__, httpx.Client.send, httpx.Client.close
    def construct(obj, *a, **kw):
        return recorder.measure("http.client_construct",lambda:original_init(obj,*a,**kw))
    def send(obj, request, *a, **kw):
        def trace(name, info):
            if name in {"connection.connect_tcp.started", "connection.connect_tcp.complete"}:
                recorder.add("http."+name,time.perf_counter())
        request.extensions["trace"] = trace
        return recorder.measure("http.send_to_body_complete",lambda:original_send(obj,request,*a,**kw))
    def close(obj):
        return recorder.measure("http.client_close",lambda:original_close(obj))
    try:
        report["runtime"] = [inspect_candidate("aicshub-gpu-embedding-candidate",8013,"11434/tcp"),
                             inspect_candidate("aicshub-gpu-v2m3-candidate",7999,"7997/tcp")]
        _, runtime, owner = verified_runtime(ROOT,reuse_m3=True)
        report["test_owner"] = owner
        client = redis.Redis(host="127.0.0.1",port=int(runtime["ports"]["redis"]),
                             socket_timeout=.2,socket_connect_timeout=.2)
        assert client.ping()
        coordinator = admission.RedisCoordinator(client,"aicshub:test:capacity:{"+uuid.uuid4().hex+"}")
        # 固定模型目录，健康响应不算推理成功。
        with httpx.Client(timeout=3,trust_env=False) as health:
            tags = health.get("http://127.0.0.1:8013/api/tags").raise_for_status().json()
            report["embedding_digest"] = next(m["digest"] for m in tags["models"] if m["name"] == "qwen3-embedding:0.6b")
            models = health.get("http://127.0.0.1:7999/models").raise_for_status().json()
            assert any(m["id"] == "BAAI/bge-reranker-v2-m3" for m in models["data"])
        cases = workload(args.count)
        serialized = json.dumps(cases,ensure_ascii=False,sort_keys=True).encode()
        report["workload_sha256"] = hashlib.sha256(serialized).hexdigest()
        (output/"synthetic-workload.json").write_bytes(serialized)
        sampler.start()
        with (patch.object(admission,"get_coordinator",return_value=coordinator),
              patch.object(httpx.Client,"__init__",construct), patch.object(httpx.Client,"send",send),
              patch.object(httpx.Client,"close",close), scheduled_services() as shared):
            warmup = {**cases[0],"case_id":"warmup","query":"warmup-only:"+cases[0]["query"]}
            report["warmup"] = timed_case(warmup,"shared",shared)
            paired = {"per_case":[],"shared":[]}
            durations = {"per_case":0.,"shared":0.}
            for i, case in enumerate(cases):
                # 每个shared查询只运行一次，避免文本缓存污染；顺序交替减少固定先后偏差。
                modes = ("per_case","shared") if i % 2 == 0 else ("shared","per_case")
                for mode in modes:
                    began = time.perf_counter()
                    paired[mode].append(timed_case(case,mode,shared if mode == "shared" else None))
                    durations[mode] += time.perf_counter()-began
                print(f"paired={i+1}/{len(cases)}",flush=True)
            for mode, rows in paired.items():
                report["groups"].append({"mode":mode,"concurrency":1,"rows":rows,
                                         "summary":group_summary(rows,len(cases),durations[mode])})
            # 显式provider探针不使用应用调度，仅这两个已核实候选的合成负载，最多2路。
            with httpx.Client(timeout=5,trust_env=False) as provider:
                for concurrency in (1,2):
                    began = time.perf_counter()
                    with ThreadPoolExecutor(max_workers=concurrency) as pool:
                        rows = list(pool.map(lambda case:timed_case(case,"provider",http_client=provider),cases))
                    report["groups"].append({"mode":"provider","concurrency":concurrency,"rows":rows,
                                             "bypasses_application_admission":True,
                                             "summary":group_summary(rows,len(cases),time.perf_counter()-began)})
                    print(f"provider concurrency={concurrency} passed={sum(r['status']=='PASS' for r in rows)}/{len(rows)}",flush=True)
                    # 不自动加压：任意失败后停止后续更高并发，不重试失败样本。
                    if any(row["status"] != "PASS" for row in rows):
                        report["stopped_after_provider_failure"] = True
                        break
        report["status"] = "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE" if len(report["groups"]) == 4 else "INCOMPLETE"
    except Exception as exc:
        report["status"], report["error_type"] = "FAIL",type(exc).__name__
    finally:
        stop.set()
        if sampler.is_alive():sampler.join(timeout=4)
        if client is not None:client.close()
        report["events"] = recorder.rows
        report["stage_summary"] = {}
        for stage in sorted({r["stage"] for r in recorder.rows}):
            rows = [r for r in recorder.rows if r["stage"] == stage]
            report["stage_summary"][stage] = {"count":len(rows),"errors":sum(r.get("outcome")=="error" for r in rows),
                                              "p95_ms":percentile([r["elapsed_ms"] for r in rows])}
        report["limitations"] = ["finite synthetic development load, not independent quality or200 acceptance",
                                 "provider2 bypasses app admission only in this guarded diagnostic; no production change",
                                 "HTTP wait includes queue/network/compute; no per-request server cancellation proof",
                                 "shared cases unique queries; no healthy query-cache hits used as inference",
                                 "paired measurements not exclusive-host randomized benchmark; sampled GPU memory only"]
        after = snapshot_sources(ROOT)
        report.update(compare_sources(before,after))
        if not report["source_tree_stable"] or not report["source_snapshot_complete"]:report["status"]="FAIL_SOURCE_DRIFT"
        for name,data in [("report",report),("source-start",before),("source-end",after),("memory-samples",samples)]:
            (output/(name+".json")).write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":report["status"],"report":str(output/"report.json")}))
    return 0 if report["status"] == "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
