"""批次压力对照完整性及临时端点实际派发；不访问网络或GPU。"""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from scripts.compare_batch_pressure import compare, validate_group
from scripts.verify_gpu_pair import scheduled_services


def fixture_groups():
    result = []
    for variant, latency in [("batch4-A1", 2900), ("batch8-B", 1800), ("batch4-A2", 3000)]:
        result.append({"variant":variant,"workload_sha256":"same", "groups":[
            {"concurrency":c,"rows":[{"case_id":str(i),"status":"PASS"} for i in range(100)],
             "summary":{"observed":100,"passed":100,"rerank_p95_ms_all_attempts":latency}}
            for c in (1, 10)]})
    return result


def test_comparison_retains_original_gate_and_narrow_scope():
    result = compare(fixture_groups())
    assert result["batch8_meets_original_latency_gate"]
    assert result["batch8_faster_than_both_controls"]
    assert result["whole_m5_pass"] is result["business_switch_approved"] is False


@pytest.mark.parametrize("bad", ["missing", "duplicate", "failed", "busy", "workload", "nan", "variant"])
def test_partial_mismatched_or_failed_runs_cannot_support_comparison(bad):
    groups = deepcopy(fixture_groups())
    if bad == "missing": groups.pop()
    elif bad == "duplicate": groups[1]["groups"][1]["rows"][1]["case_id"] = "0"
    elif bad == "failed": groups[1]["groups"][1]["rows"][1]["status"] = "FAIL"
    elif bad == "busy": groups[1]["groups"][1]["rows"][1]["status"] = "BUSY"
    elif bad == "workload": groups[1]["workload_sha256"] = "different"
    elif bad == "nan": groups[1]["groups"][1]["summary"]["rerank_p95_ms_all_attempts"] = float("nan")
    else: groups[1]["variant"] = "batch16"
    with pytest.raises((ValueError, RuntimeError)):
        compare(groups)


def test_incomplete_child_stops_before_more_load():
    with pytest.raises(RuntimeError): validate_group({"groups":[]})


def test_temporary_port_reaches_actual_provider_and_closes_services(monkeypatch):
    embedding, reranker = Mock(), Mock()
    factory = Mock(return_value=reranker)
    monkeypatch.setattr("app.services.embedding_service.APIEmbedding", Mock(return_value=embedding))
    monkeypatch.setattr("app.services.reranker_service.RerankerService", factory)
    with scheduled_services(55555) as services:
        assert services == (embedding, reranker)
        assert factory.call_args.kwargs["local_base"] == "http://127.0.0.1:55555"
    embedding.close.assert_called_once()
    reranker.close.assert_called_once()
