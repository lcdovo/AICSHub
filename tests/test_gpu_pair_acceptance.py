import pytest
from scripts.verify_gpu_pair import percentile, summarize, validate_scores, workload, inspect_candidate


def test_workload_preserves_twenty_long_candidates_without_claiming_semantic_independence():
    rows = workload(200)
    assert len(rows) == len({r["query"] for r in rows}) == 200
    assert len({r["cluster"] for r in rows}) == 12
    assert all(len(r["documents"]) == 20 for r in rows)
    assert min(len(t) for r in rows for t in r["documents"]) > 300
    assert max(len(t) for r in rows for t in r["documents"]) < 2000


@pytest.mark.parametrize("value", [[], [{"index": True, "score": .9}],
    [{"index": 0, "score": float("nan")}],
    [{"index": 0, "score": .9}, {"index": 0, "score": .8}]])
def test_malformed_or_partial_rerank_cannot_pass(value):
    with pytest.raises(ValueError):
        validate_scores(value, 2)


def test_timeout_stays_in_latency_denominator_and_blocks_gate():
    rows = [{"case_id": "1", "status": "PASS", "total_ms": 1000, "rerank_ms": 900},
            {"case_id": "2", "status": "FAIL", "total_ms": 5100, "rerank_ms": 5000}]
    result = summarize(rows, 2)
    assert result["status"] == "FAIL" and result["failed"] == 1
    assert result["rerank_p95_ms_all_attempts"] == 5000


def test_missing_results_and_empty_reports_never_pass():
    assert summarize([], 100)["status"] == "FAIL"
    assert summarize([], 0)["status"] == "FAIL"
    rows = [{"case_id": "1", "status": "PASS", "total_ms": 1000, "rerank_ms": 900}]
    assert summarize(rows, 100)["missing"] == 99
    assert summarize(rows * 2, 2)["status"] == "FAIL"


def test_passing_http_without_rerank_observation_cannot_pass_performance_gate():
    rows = [{"case_id": "1", "status": "PASS", "total_ms": 1000}]
    assert summarize(rows, 1)["status"] == "FAIL"


def test_p95_preserves_upper_tail_and_uses_nearest_rank():
    assert percentile(list(range(1, 101))) == 95
    assert percentile([]) is None


def test_complete_fast_results_only_establish_finite_provider_scope():
    rows = [{"case_id": str(i), "status": "PASS", "total_ms": 1400, "rerank_ms": 1000}
            for i in range(100)]
    result = summarize(rows, 100)
    assert result["status"] == "PASS_FINITE_LOAD_ONLY"
    assert result["passed"] == result["observed"] == 100 and result["failed"] == 0


@pytest.mark.parametrize("invalid", ["owner", "stopped", "no_gpu", "public_binding", "wrong_port"])
def test_runtime_guard_rejects_wrong_owner_state_gpu_or_endpoint(monkeypatch, invalid):
    import json
    obj = {"Config": {"Labels": {"aicshub.test.owner": "gpu-model-switch"}, "Cmd": ["serve"]},
           "State": {"Status": "running"}, "HostConfig": {"DeviceRequests": [{"Capabilities": [["gpu"]]}]},
           "NetworkSettings": {"Ports": {"11434/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8013"}]}},
           "Id": "synthetic-container", "Image": "synthetic-image"}
    if invalid == "owner":
        obj["Config"]["Labels"]["aicshub.test.owner"] = "other-owner"
    elif invalid == "stopped":
        obj["State"]["Status"] = "exited"
    elif invalid == "no_gpu":
        obj["HostConfig"]["DeviceRequests"] = None
    elif invalid == "public_binding":
        obj["NetworkSettings"]["Ports"]["11434/tcp"][0]["HostIp"] = "0.0.0.0"
    else:
        obj["NetworkSettings"]["Ports"]["11434/tcp"][0]["HostPort"] = "8012"
    monkeypatch.setattr("scripts.verify_gpu_pair.subprocess.check_output",
                        lambda *a, **kw: json.dumps([obj]))
    with pytest.raises(RuntimeError, match="unverified_gpu_candidate"):
        inspect_candidate("synthetic", 8013, "11434/tcp")
