"""隔离诊断协调IO/释放/客户端HTTP；不修改业务配置，不作完整性能验收。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CASE = ContextVar("diagnostic_case", default="outside_request")


class Recorder:
    def __init__(self):
        self.started = time.perf_counter()
        self.rows = []
        self.lock = threading.Lock()

    def add(self, stage, began, **values):
        row = {"case": CASE.get(), "stage": stage,
               "at_ms": round((began-self.started)*1000, 3),
               "elapsed_ms": round((time.perf_counter()-began)*1000, 3), **values}
        with self.lock:
            self.rows.append(row)

    def measure(self, stage, call, **values):
        began = time.perf_counter()
        try:
            result = call()
        except Exception as exc:
            self.add(stage, began, outcome="error", error_type=type(exc).__name__, **values)
            raise
        self.add(stage, began, outcome="ok", **values)
        return result


class RedisProbe:
    """不保存Redis地址、命令参数、原始token或用户内容。"""
    def __init__(self, client, recorder):
        self.client, self.recorder = client, recorder

    def invoke(self, stage, call, token=None):
        began = time.perf_counter()
        values = {"lease": hashlib.sha256(token.encode()).hexdigest()[:12]} if token else {}
        try:
            result = call()
        except Exception as exc:
            self.recorder.add(stage, began, outcome="error", error_type=type(exc).__name__, **values)
            raise
        # GET only reports presence. Equality is checked by the real Permit implementation.
        value = bool(result) if stage == "redis.owner_get" else result
        self.recorder.add(stage, began, outcome="ok", result=value, **values)
        return result

    def eval(self, script, n, *args):
        from app.services.local_admission import ACQUIRE, RELEASE
        stage = "acquire" if script == ACQUIRE else "release" if script == RELEASE else "renew"
        return self.invoke("redis."+stage, lambda: self.client.eval(script, n, *args), args[n])

    def get(self, key):
        return self.invoke("redis.owner_get", lambda: self.client.get(key))

    def zrem(self, key, token):
        return self.invoke("redis.ticket_cleanup", lambda: self.client.zrem(key, token), token)


def safe_error(exc):
    from app.services.local_inference import LocalModelBusy, LocalModelDeadline
    return str(exc) if isinstance(exc, (LocalModelBusy, LocalModelDeadline)) else type(exc).__name__


def main():
    import httpx
    import redis
    from app.services import local_admission as admission
    from app.services.local_inference import inference_scope
    from scripts.test_m1_real import verified_runtime
    from scripts.test_provenance import snapshot_sources, compare_sources
    from scripts.verify_gpu_pair import inspect_candidate, run_case_scheduled, workload, percentile

    before = snapshot_sources(ROOT)
    output = ROOT / "reports/gpu-pair" / datetime.now().strftime("io-diagnosis-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    _, runtime, owner = verified_runtime(ROOT, reuse_m3=True)
    namespace = "aicshub:test:diagnosis:{"+uuid.uuid4().hex+"}"
    client = redis.Redis(host="127.0.0.1", port=int(runtime["ports"]["redis"]),
                         socket_timeout=.2, socket_connect_timeout=.2)
    recorder = Recorder()
    coordinator = admission.RedisCoordinator(RedisProbe(client, recorder), namespace)
    report = {"kind": "diagnosis_not_acceptance", "business_changed": False,
              "external_paid_calls": 0, "verified_test_owner": owner, "redis_version": redis.__version__,
              "thresholds_changed": False, "groups": [], "snapshots": [],
              "runtime": [inspect_candidate("aicshub-gpu-embedding-candidate", 8013, "11434/tcp"),
                          inspect_candidate("aicshub-gpu-v2m3-candidate", 7999, "7997/tcp")]}
    # Default retry state without printing the connection's credentials.
    connection = client.connection_pool.get_connection("PING")
    report["redis_retry"] = {"attempts_after_first": connection.retry._retries,
                             "retry_on_error": [t.__name__ for t in connection.retry_on_error]}
    client.connection_pool.release(connection)
    original_init, original_send, original_close = httpx.Client.__init__, httpx.Client.send, httpx.Client.close
    original_check, original_permit_close = admission.Permit.check, admission.Permit.close

    def init_http(obj, *args, **kwargs):
        return recorder.measure("http.client_construct", lambda: original_init(obj, *args, **kwargs))

    def send_http(obj, request, *args, **kwargs):
        # HTTPcore event names and timestamps only, never headers/body/URL.
        def trace(name, info):
            recorder.add("http.trace."+name, time.perf_counter())
        request.extensions["trace"] = trace
        return recorder.measure("http.send_to_body_complete", lambda: original_send(obj, request, *args, **kwargs))

    def close_http(obj):
        return recorder.measure("http.client_close", lambda: original_close(obj))

    def check(permit):
        began = time.perf_counter()
        try:
            return original_check(permit)
        except Exception as exc:
            recorder.add("permit.check_failure", began, reason=safe_error(exc),
                         lost=permit._lost, deadline_expired=time.monotonic() >= permit.deadline)
            raise

    def close_permit(permit):
        began = time.perf_counter()
        try:
            return original_permit_close(permit)
        finally:
            recorder.add("permit.close", began, refs=permit._refs, closed=permit._closed,
                         released=permit._released, lost=permit._lost)

    def snapshot(label):
        # Only read this freshly created test namespace and public model health/metrics.
        began = time.perf_counter()
        try:
            with client.pipeline(transaction=False) as pipe:
                pipe.get(coordinator.keys[0]).pttl(coordinator.keys[0])
                pipe.zcard(coordinator.keys[1]).zcard(coordinator.keys[2])
                value, ttl, front, back = pipe.execute()
            state = {"owner": hashlib.sha256(value).hexdigest()[:12] if value else None,
                     "ttl_ms": ttl, "interactive": front, "background": back}
        except Exception as exc:
            state = {"error_type": type(exc).__name__}
        with httpx.Client(timeout=3, trust_env=False) as http:
            for name, url in [("reranker", "http://127.0.0.1:7999/metrics"),
                              ("embedding", "http://127.0.0.1:8013/api/ps")]:
                try:
                    response = http.get(url).raise_for_status()
                    if name == "reranker":
                        # Public service counters, including any available queue/batch metrics.
                        state[name] = [line for line in response.text.splitlines() if not line.startswith("#")
                                       and any(s in line for s in ("infinity", "queue", "batch", "http_request_duration", "http_requests_total"))]
                    else:
                        state[name] = [{k: m.get(k) for k in ("name", "size", "size_vram")} for m in response.json().get("models", [])]
                except Exception as exc:
                    state[name] = {"error_type": type(exc).__name__}
        report["snapshots"].append({"label": label, "at_ms": (began-recorder.started)*1000, **state})

    def case_run(case):
        token = CASE.set(case["diagnostic_id"])
        try:
            return {**run_case_scheduled(case, whole_request=True), "diagnostic_id": case["diagnostic_id"]}
        finally:
            CASE.reset(token)

    try:
        snapshot("before")
        with patch.object(admission, "get_coordinator", return_value=coordinator):
            for i in range(50):
                token = CASE.set(f"redis-only-{i}")
                try:
                    with inference_scope(events=[]), admission.request_scope():
                        pass
                finally:
                    CASE.reset(token)
        report["redis_only_rounds"] = 50
        # Same fixtures, interleaved process/redis blocks; original deadlines/candidate sizes.
        cases = workload(10)
        with (patch.object(httpx.Client, "__init__", init_http),
              patch.object(httpx.Client, "send", send_http), patch.object(httpx.Client, "close", close_http),
              patch.object(admission.Permit, "check", check), patch.object(admission.Permit, "close", close_permit)):
            for scope, concurrency in [("process", 1), ("redis", 1), ("redis", 1), ("process", 1), ("redis", 10)]:
                target = coordinator if scope == "redis" else admission.ProcessCoordinator()
                group_cases = [{**case, "diagnostic_id": f"block-{len(report['groups'])}-{case['case_id']}"}
                               for case in cases]
                with patch.object(admission, "get_coordinator", return_value=target):
                    with ThreadPoolExecutor(max_workers=concurrency) as pool:
                        rows = list(pool.map(case_run, group_cases))
                report["groups"].append({"scope": scope, "concurrency": concurrency, "rows": rows})
                print(f"{scope} concurrency={concurrency} passed={sum(r['status']=='PASS' for r in rows)}/{len(rows)}", flush=True)
                snapshot(f"after-{scope}-{len(report['groups'])}")
        # No forced inference timeout here; historical timeouts cannot prove server cancellation.
        report["limitations"] = ["50 finite local-model requests, not 200-request acceptance",
                                  "HTTP wait includes server queue+inference; cannot separate without server per-request spans",
                                  "no exclusive host resources, no historical same-time server queue samples",
                                  "service metrics snapshots cannot prove cancellation of historical timed-out requests"]
    finally:
        snapshot("final")
        report["events"] = recorder.rows
        report["stage_summary"] = {}
        for stage in sorted({r["stage"] for r in recorder.rows}):
            values = [r for r in recorder.rows if r["stage"] == stage]
            report["stage_summary"][stage] = {"count": len(values),
                "errors": sum(r.get("outcome") == "error" for r in values),
                "p95_ms": percentile([r["elapsed_ms"] for r in values]),
                "max_ms": max(r["elapsed_ms"] for r in values)}
        report.update(compare_sources(before, snapshot_sources(ROOT)))
        (output / "report.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
        (output / "source-start.json").write_text(json.dumps(before, indent=2)+"\n", encoding="utf-8")
        client.close()
        print(output, flush=True)


if __name__ == "__main__":
    main()
