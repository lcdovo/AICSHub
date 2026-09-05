"""第 6 步：Trace、评测持久化和指标出口回归。"""
import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models.models import AgentTrace, EvaluationResult
from app.utils.database import async_session
from app.utils.tracking import get_tracer


def _login(client, username="user001", password="user12345"):
    response = client.post("/api/v1/users/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def test_trace_id_spans_evaluation_and_metrics_endpoint():
    trace_id = "observability-regression-trace"
    with TestClient(app) as client:
        headers = {**_login(client), "X-Trace-Id": trace_id}
        response = client.post("/api/v1/chat/send", json={"user_id": 1, "message": "请查询订单"}, headers=headers)
        assert response.status_code == 200
        assert response.json()["trace_id"] == trace_id

        admin_headers = _login(client, "admin", "admin12345")
        trace_response = client.get(f"/api/v1/analytics/traces/{trace_id}", headers=admin_headers)
        assert trace_response.status_code == 200
        assert trace_response.json()["data"]["span_count"] >= 1
        metrics = client.get("/metrics", headers=admin_headers)
        assert metrics.status_code == 200
        assert "app_requests_total" in metrics.text

    async def verify_rows():
        async with async_session() as db:
            span = await db.scalar(select(AgentTrace).where(AgentTrace.request_trace_id == trace_id))
            evaluation = await db.scalar(select(EvaluationResult).where(EvaluationResult.trace_id == trace_id))
            assert span is not None
            assert span.span_id
            assert evaluation is not None
            assert evaluation.evaluator == "heuristic"
            assert evaluation.prompt_version == "v1"
            assert evaluation.latency_ms is not None

    asyncio.run(verify_rows())


def test_metrics_collector_exposes_rag_model_and_handoff_rates():
    tracer = get_tracer()
    tracer.reset_metrics()
    tracer._metrics.record_rag(latency_ms=12, empty=True, backend="memory", degraded=True)
    tracer._metrics.record_model(model="mock", tokens=42, latency_ms=3, success=True)
    tracer._metrics.record_handoff()
    metrics = tracer.get_metrics()
    assert metrics["rag"]["empty"] == 1
    assert metrics["rag"]["degraded"] == 1
    assert metrics["model"]["tokens"] == 42
    assert "app_rag_empty_rate" in tracer._metrics.to_prometheus()
