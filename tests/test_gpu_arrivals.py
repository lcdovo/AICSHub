"""负载生成器不可把积压、漏样本或失败隐藏成健康吞吐。"""
import json
import threading

import pytest

from scripts.evaluate_gpu_arrivals import arrival_summary, run_arrivals, verify_reranker


@pytest.mark.parametrize("rate",[0,-1,float("inf"),float("nan")])
def test_invalid_rates_never_start_load(rate):
    with pytest.raises(ValueError):run_arrivals([],rate,lambda _:None)


def test_generator_limits_inflight_and_keeps_dropped_arrival():
    entered,release=threading.Event(),threading.Event()
    def call(case):
        entered.set()
        assert release.wait(2)
        return {"status":"PASS","rerank_ms":1}
    def cases():
        yield {"case_id":"1"}
        assert entered.wait(1)
        yield {"case_id":"2"}
        # driver处理完第二次到达才释放首请求，不依赖机器速度或固定sleep。
        release.set()
    try:
        rows,timing=run_arrivals(cases(),1,call,max_inflight=1,sleep=lambda _:None)
    finally:
        release.set()
    assert timing["peak_inflight"]==1
    assert sorted(row["status"] for row in rows)==["LOAD_GENERATOR_BUSY","PASS"]
    summary=arrival_summary(rows,2,timing)
    assert summary["observed"]==2 and summary["generator_rejected"]==1
    assert summary["status"]=="FAIL_LOAD_GENERATOR"


def test_exception_is_one_failed_row_without_private_message():
    def fail(_):raise RuntimeError("private-user-text")
    rows,timing=run_arrivals([{"case_id":"synthetic"}],1,fail)
    assert len(rows)==1 and rows[0]["error_type"]=="RuntimeError"
    assert "private" not in json.dumps(rows)
    assert arrival_summary(rows,1,timing)["status"]=="FAIL"


def test_rate_schedule_and_lag_do_not_become_silent_executor_queue():
    clock=[100.]
    waits=[]
    def sleep(seconds):
        waits.append(seconds)
        clock[0]+=seconds
    def call(case):return {"status":"PASS","rerank_ms":1}
    rows,timing=run_arrivals([{"case_id":str(i)} for i in range(3)],2,call,clock=lambda:clock[0],sleep=sleep)
    assert [r["planned_s"] for r in rows]==[0,.5,1.]
    assert all(r["submit_lag_ms"]==0 for r in rows)
    rows[0]["submit_lag_ms"]=100
    assert arrival_summary(rows,3,timing)["status"]=="FAIL_LOAD_GENERATOR"


@pytest.mark.parametrize("invalid",["owner","state","gpu","binding","name"])
def test_batch_endpoint_requires_owned_running_loopback_gpu(monkeypatch,invalid):
    obj={"Config":{"Labels":{"aicshub.test.owner":"gpu-batch-capacity"},"Cmd":["v2"]},
         "State":{"Status":"running"},"HostConfig":{"DeviceRequests":[{}]},
         "NetworkSettings":{"Ports":{"7997/tcp":[{"HostIp":"127.0.0.1","HostPort":"55000"}]}},
         "Id":"synthetic","Image":"synthetic-image"}
    name="aicshub-batch-capacity-synthetic"
    if invalid=="owner":obj["Config"]["Labels"]["aicshub.test.owner"]="other"
    elif invalid=="state":obj["State"]["Status"]="exited"
    elif invalid=="gpu":obj["HostConfig"]["DeviceRequests"]=[]
    elif invalid=="binding":obj["NetworkSettings"]["Ports"]["7997/tcp"][0]["HostIp"]="0.0.0.0"
    else:name="other-project"
    monkeypatch.setattr("scripts.evaluate_gpu_arrivals.subprocess.check_output",lambda *a,**kw:json.dumps([obj]))
    with pytest.raises(RuntimeError,match="unverified_batch_candidate"):verify_reranker(name,55000)


def test_batch_command_changes_only_verified_batch_argument():
    from scripts.compare_gpu_batch import batch_command
    command=["v2","--model-id","BAAI/bge-reranker-v2-m3","--device","cuda","--dtype","float16",
             "--port","7997","--batch-size","4","--no-compile","--no-model-warmup"]
    expected=list(command)
    expected[expected.index("--batch-size")+1]="8"
    assert batch_command(command,8)==expected
    assert command[command.index("--batch-size")+1]=="4"
    with pytest.raises(RuntimeError):batch_command(command+["--batch-size","4"],8)
    with pytest.raises(RuntimeError):batch_command(command,16)


def test_batch_health_failure_still_stops_temporary_and_restores_original(monkeypatch):
    from scripts import compare_gpu_batch as module
    command=["v2","--model-id","BAAI/bge-reranker-v2-m3","--device","cuda","--dtype","float16",
             "--port","7997","--batch-size","4","--no-compile","--no-model-warmup"]
    verified={"id":"old","name":module.ORIGINAL}
    original={"Id":"old","Image":"same-image","Config":{"Cmd":command},
              "Mounts":[{"Destination":"/app/.cache/huggingface","Name":"aicustomer_reranker_hf_cache"}]}
    temp={"Id":"new","Image":"same-image","Config":{"Labels":{"aicshub.test.owner":"gpu-batch-capacity"}},
          "Mounts":[{"Destination":"/app/.cache/huggingface","RW":False}],
          "NetworkSettings":{"Ports":{"7997/tcp":[{"HostIp":"127.0.0.1","HostPort":"55000"}]}},
          "State":{"Running":True,"Status":"exited"}}
    calls=[]
    def docker(*args,**kwargs):
        calls.append(args)
        if args[0]=="inspect":return json.dumps([temp if args[1]=="new" else original])
        if args[0]=="create":return "new"
        return ""
    def health(port,*args):
        if port==55000:raise RuntimeError("synthetic-health-failure")
        return True
    monkeypatch.setattr(module,"docker",docker)
    monkeypatch.setattr(module,"health",health)
    monkeypatch.setattr("scripts.verify_gpu_pair.inspect_candidate",lambda *args:verified)
    report={}
    with pytest.raises(RuntimeError,match="synthetic-health-failure"):
        with module.isolated_batch(report):pytest.fail("unhealthy candidate cannot run load")
    assert ("stop","--time","10","new") in calls
    assert ("start","old") in calls and report["original_restored_healthy"]
    create=next(c for c in calls if c[0]=="create")
    assert "HF_HUB_OFFLINE=1" in create
    assert any("readonly" in arg for arg in create)
