"""生命周期所有权、完整负载与诊断范围；不访问真实模型。"""
from unittest.mock import Mock

import pytest

from scripts import verify_gpu_pair as module
from scripts.diagnose_gpu_capacity import group_summary, timed_case


def services(monkeypatch):
    embedding, reranker = Mock(), Mock()
    reranker.rerank_with_deadline.return_value = [{"id":str(i)} for i in range(20)]
    monkeypatch.setattr("app.services.embedding_service.APIEmbedding",Mock(return_value=embedding))
    monkeypatch.setattr("app.services.reranker_service.RerankerService",Mock(return_value=reranker))
    return embedding, reranker


def test_shared_services_are_not_closed_by_individual_cases(monkeypatch):
    embedding, reranker = services(monkeypatch)
    with module.scheduled_services() as pair:
        for case in module.workload(2):
            assert module.run_case_scheduled(case,services=pair)["status"] == "PASS"
        embedding.close.assert_not_called()
        reranker.close.assert_not_called()
    embedding.close.assert_called_once()
    reranker.close.assert_called_once()
    assert embedding.encode_query.call_count == reranker.rerank_with_deadline.call_count == 2
    assert all(len(call.args[1]) == 20 for call in reranker.rerank_with_deadline.call_args_list)


def test_per_case_services_close_even_when_encoding_fails(monkeypatch):
    embedding, reranker = services(monkeypatch)
    embedding.encode_query.side_effect = RuntimeError("private-body")
    row = module.run_case_scheduled(module.workload(1)[0])
    assert row["status"] == "FAIL" and row["error_type"] == "RuntimeError"
    assert "private" not in str(row)
    embedding.close.assert_called_once()
    reranker.close.assert_called_once()


def test_partial_constructor_and_close_failure_still_release_embedding(monkeypatch):
    embedding, reranker = services(monkeypatch)
    reranker.close.side_effect = RuntimeError("synthetic")
    with pytest.raises(RuntimeError):
        with module.scheduled_services():pass
    embedding.close.assert_called_once()
    embedding.reset_mock()
    monkeypatch.setattr("app.services.reranker_service.RerankerService",Mock(side_effect=ValueError("init")))
    with pytest.raises(ValueError):
        with module.scheduled_services():pass
    embedding.close.assert_called_once()


def test_raw_provider_injection_uses_supplied_client_and_retains_full_scores():
    import httpx
    import json
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if request.url.path.endswith("embeddings"):
            return httpx.Response(200,json={"data":[{"index":0,"embedding":[1.]*1024}]})
        return httpx.Response(200,json={"results":[{"index":i,"score":1.} for i in range(20)]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        row = module.run_case(module.workload(1)[0],http_client=client)
        assert row["status"] == "PASS" and not client.is_closed
    assert len(calls[1]["documents"]) == calls[1]["top_n"] == 20


def test_diagnostic_routes_provider_explicitly_without_claiming_app_admission(monkeypatch):
    run = Mock(return_value={"status":"FAIL","case_id":"synthetic","total_ms":5000})
    monkeypatch.setattr(module,"run_case",run)
    row = timed_case({"case_id":"synthetic"},"provider",http_client="synthetic-client")
    assert run.call_args.kwargs["scheduled"] is False
    assert run.call_args.kwargs["whole_request"] is False
    assert row["ended"] >= row["started"]
    result = group_summary([row],1,5)
    assert result["status"] == "FAIL" and result["completed_per_second"] == 0
