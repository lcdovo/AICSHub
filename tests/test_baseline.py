from fastapi.testclient import TestClient

from app.main import app
from app.agent.graph import AgentGraph
from app.agent.state import AgentState


def test_health_reports_local_degraded_dependencies():
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert payload["mode"] == "local"
    assert payload["dependencies"]["database"]["status"] == "healthy"
    assert payload["dependencies"]["milvus"]["backend"] == "memory"
    assert payload["dependencies"]["llm"]["backend"] == "mock"


def test_root_and_openapi_are_available():
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_chat_api_smoke_uses_mock_llm():
    with TestClient(app) as client:
        login = client.post("/api/v1/users/login", json={"username": "user001", "password": "user12345"})
        assert login.status_code == 200
        token = login.json()["data"]["access_token"]
        response = client.post(
            "/api/v1/chat/send",
            json={"user_id": 1, "message": "请查询订单"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"]
    assert payload["reply"]
    assert payload["intent"] == "query_order"


async def test_agent_minimal_mock_flow():
    graph = AgentGraph()
    state = AgentState(session_id="baseline-session", user_id=1, user_message="请查询订单")
    result = await graph.run(state)

    assert result.reply
    assert result.detected_intent in {"query_order", "general"}
    assert result.execution_time_ms >= 0
    await graph.llm_service.close()
