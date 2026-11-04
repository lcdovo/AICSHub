"""只使用m3-closeout专用Redis及随机namespace的真实多进程准入验证。"""
from __future__ import annotations

from datetime import datetime
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def client(port):
    import redis
    return redis.Redis(host="127.0.0.1",port=port,decode_responses=True,socket_timeout=1,socket_connect_timeout=1)


def worker(port,namespace,ready,start,events):
    from app.services.local_admission import RedisCoordinator
    coordinator=RedisCoordinator(client(port),namespace,ttl_ms=1500)
    ready.put(os.getpid())
    start.wait(10)
    try:
        for i in range(5):
            permit=coordinator.acquire(priority="interactive",deadline=time.monotonic()+5)
            try:
                def call(_):
                    began=time.monotonic()
                    time.sleep(.03)
                    return began,time.monotonic()
                began,ended=permit.http(call,purpose="synthetic_embedding_and_rerank")
                time.sleep(.02)
                _,ended=permit.http(call,purpose="synthetic_rerank")
                events.put({"pid":os.getpid(),"round":i,"start":began,"end":ended,"http_calls":2})
            finally:
                permit.close()
    except Exception as exc:
        events.put({"pid":os.getpid(),"error_type":type(exc).__name__})


def crash_owner(port,namespace,ready):
    from app.services.local_admission import RedisCoordinator
    coordinator=RedisCoordinator(client(port),namespace,ttl_ms=700)
    permit=coordinator.acquire(priority="interactive",deadline=time.monotonic()+5)
    ready.put({"token":permit.token,"pid":os.getpid()})
    time.sleep(.1)
    os._exit(0)


def main():
    from scripts.test_m1_real import verified_runtime
    from scripts.test_provenance import snapshot_sources,compare_sources
    from app.services.local_admission import RedisCoordinator, RELEASE
    from app.services.local_inference import LocalModelBusy,LocalModelDeadline
    before=snapshot_sources(ROOT)
    _,state,owner=verified_runtime(ROOT,reuse_m3=True)
    port=int(state["ports"]["redis"])
    output=ROOT/"reports/gpu-pair"/datetime.now().strftime("redis-admission-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    (output/"source-start.json").write_text(json.dumps(before,indent=2),encoding="utf-8")
    report={"status":"FAIL","verified_test_owner":owner,"business_resources_used":False,
            "external_model_calls":0,"scope":"actual Redis and child processes, no GPU calls","cases":[]}
    try:
        redis=client(port)
        assert redis.ping()
        namespace="aicshub:test:local-admission:"+uuid.uuid4().hex
        from app.services.local_admission import ACQUIRE
        coordinator=RedisCoordinator(redis,namespace+":replay",ttl_ms=1500)
        ticket=uuid.uuid4().hex
        first=redis.eval(ACQUIRE,3,*coordinator.keys,ticket,"interactive",500,1500,1,2)
        duplicate=redis.eval(ACQUIRE,3,*coordinator.keys,ticket,"interactive",500,1500,1,2)
        assert first==duplicate==1
        coordinator.release(ticket)
        report["cases"].append({"case":"duplicate_acquisition_after_lost_reply_is_idempotent","status":"PASS"})
        class LostReply:
            def eval(self,script,*args):
                value=redis.eval(script,*args)
                if script==ACQUIRE:
                    raise ConnectionError("synthetic_lost_reply_after_successful_write")
                return value
            def get(self,*args):return redis.get(*args)
            def zrem(self,*args):return redis.zrem(*args)
        coordinator=RedisCoordinator(LostReply(),namespace+":lost-reply",ttl_ms=1500)
        try:coordinator.acquire(priority="interactive",deadline=time.monotonic()+3)
        except LocalModelBusy:pass
        else:raise AssertionError("lost reply must not dispatch")
        assert redis.get(coordinator.keys[0]) is None
        report["cases"].append({"case":"lost_reply_after_write_conditionally_cleans_own_undispatched_owner","status":"PASS"})
        # 实际Redis键+客户端定点故障，分别核对删除、未知和继任者安全。
        class ReleaseFault:
            def __init__(self, mode):
                self.mode, self.calls = mode, 0
            def eval(self, script, *args):
                if script == RELEASE:
                    self.calls += 1
                    if self.mode == "persistent" or self.calls == 1:
                        if self.mode == "lost_reply":
                            redis.eval(script, *args)
                        elif self.mode == "successor":
                            redis.eval(script, *args)
                            assert redis.set(args[1], "synthetic-successor", nx=True, px=1500)
                        raise TimeoutError("synthetic_release_transport_failure")
                return redis.eval(script, *args)
            def get(self, *args): return redis.get(*args)
            def zrem(self, *args): return redis.zrem(*args)
        for mode, expected in [("transient", "released"), ("lost_reply", "not_owner"),
                               ("successor", "not_owner"), ("persistent", "unknown")]:
            fault = ReleaseFault(mode)
            coordinator = RedisCoordinator(fault, namespace+":release:"+mode, ttl_ms=1500)
            permit = coordinator.acquire(priority="interactive", deadline=time.monotonic()+5)
            permit.close()
            permit.close()
            assert fault.calls == permit.release_attempts == 2
            assert permit.release_status == expected
            assert permit._released == (expected == "released")
            value = redis.get(coordinator.keys[0])
            if mode == "successor":
                assert value == "synthetic-successor"
                redis.eval(RELEASE, 1, coordinator.keys[0], "synthetic-successor")
            elif mode == "persistent":
                assert value == permit.token and redis.pttl(coordinator.keys[0]) > 0
                assert permit._stop.is_set()
                redis.eval(RELEASE, 1, coordinator.keys[0], permit.token)
            else:
                assert value is None
            report["cases"].append({"case":"release_"+mode, "status":"PASS",
                                    "release_status":expected, "release_attempts":fault.calls,
                                    "limit":"real Redis with client fault injection; server not stopped"})
        ready,start,events=mp.Queue(),mp.Event(),mp.Queue()
        processes=[mp.Process(target=worker,args=(port,namespace,ready,start,events)) for _ in range(2)]
        for p in processes:p.start()
        ids=[ready.get(timeout=20) for _ in processes]
        assert len(set(ids))==2
        start.set()
        for p in processes:
            p.join(20)
            if p.is_alive():p.terminate();p.join();raise RuntimeError("child_timeout")
            assert p.exitcode==0
        rows=[events.get(timeout=3) for _ in range(10)]
        assert all("error_type" not in row for row in rows)
        rows.sort(key=lambda r:r["start"])
        assert all(left["end"]<=right["start"] for left,right in zip(rows,rows[1:]))
        report["cases"].append({"case":"two_processes_shared_budget","status":"PASS","pids":ids,"calls":rows})

        coordinator=RedisCoordinator(redis,namespace+":stale",ttl_ms=1500)
        old=coordinator.acquire(priority="interactive",deadline=time.monotonic()+5)
        redis.eval(RELEASE,1,coordinator.keys[0],old.token)  # synthetic ownership loss, only this new test key
        successor=coordinator.acquire(priority="interactive",deadline=time.monotonic()+5)
        called=[]
        try:
            try:old.http(lambda _:called.append(True),purpose="synthetic")
            except LocalModelDeadline:pass
            else:raise AssertionError("lost owner dispatched")
            old.close()
            assert not called and coordinator.valid(successor.token)
            assert not coordinator.valid(old.token,renew=True)
            assert coordinator.valid(successor.token)
        finally:successor.close()
        report["cases"].append({"case":"stale_owner_cannot_dispatch_renew_or_release_successor","status":"PASS"})

        crash_namespace=namespace+":crash"
        ready=mp.Queue()
        p=mp.Process(target=crash_owner,args=(port,crash_namespace,ready))
        p.start();row=ready.get(timeout=20);p.join(5);assert p.exitcode==0
        time.sleep(.8)
        coordinator=RedisCoordinator(redis,crash_namespace,ttl_ms=1000)
        recovered=coordinator.acquire(priority="interactive",deadline=time.monotonic()+5)
        assert coordinator.valid(recovered.token) and recovered.token!=row["token"]
        recovered.close()
        report["cases"].append({"case":"dead_worker_lease_expires_and_new_owner_acquires","status":"PASS"})

        coordinator=RedisCoordinator(redis,namespace+":heartbeat",ttl_ms=600)
        permit=coordinator.acquire(priority="interactive",deadline=time.monotonic()+5)
        time.sleep(.9)
        assert coordinator.valid(permit.token)
        permit.close()
        assert redis.get(coordinator.keys[0]) is None
        report["cases"].append({"case":"live_renewal_survives_original_ttl_and_conditionally_releases","status":"PASS"})

        class FaultClient:
            broken=False
            def eval(self,*a):
                if self.broken:raise ConnectionError("synthetic_transport_failure")
                return redis.eval(*a)
            def get(self,*a):
                if self.broken:raise ConnectionError("synthetic_transport_failure")
                return redis.get(*a)
            def zrem(self,*a):
                if self.broken:raise ConnectionError("synthetic_transport_failure")
                return redis.zrem(*a)
        fault=FaultClient()
        coordinator=RedisCoordinator(fault,namespace+":fault",ttl_ms=1000)
        permit=coordinator.acquire(priority="interactive",deadline=time.monotonic()+5)
        fault.broken=True
        called=[]
        try:
            try:permit.http(lambda _:called.append(True),purpose="synthetic")
            except LocalModelDeadline:pass
            else:raise AssertionError("fault failed open")
            try:coordinator.acquire(priority="interactive",deadline=time.monotonic()+1)
            except LocalModelBusy:pass
            else:raise AssertionError("fault acquired")
            assert not called
        finally:permit.close()
        report["cases"].append({"case":"injected_transport_fault_fail_closed_no_process_fallback","status":"PASS",
                                "limit":"real Redis keys with client fault injection, server not stopped"})
        report["status"]="PASS_T1_SYNTHETIC_COORDINATION"
    except Exception as exc:
        report["error_type"]=type(exc).__name__
    finally:
        after=snapshot_sources(ROOT)
        report.update(compare_sources(before,after))
        if not report["source_tree_stable"] or not report["source_snapshot_complete"]:
            report["status"]="FAIL_SOURCE_DRIFT"
        (output/"source-end.json").write_text(json.dumps(after,indent=2),encoding="utf-8")
        (output/"report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":report["status"],"cases":len(report["cases"]),"error_type":report.get("error_type"),
                      "report":str(output/"report.json")}))
    return 0 if report["status"]=="PASS_T1_SYNTHETIC_COORDINATION" else 2


if __name__=="__main__":
    mp.freeze_support()
    raise SystemExit(main())
