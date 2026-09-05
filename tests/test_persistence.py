"""第 3 步：持久化、状态机和幂等回归。"""
from fastapi.testclient import TestClient

from app.main import app
from app.utils.database import async_session
from app.models.models import Ticket, HandoffRequestRecord, NotificationRecord, OperationAudit, EvaluationResult
from sqlalchemy import select


def _login(client, username="user001", password="user12345"):
    response = client.post("/api/v1/users/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def test_ticket_idempotency_state_machine_and_persistence():
    with TestClient(app) as client:
        headers = _login(client)
        key = "persist-ticket-1"
        payload = {"user_id": 1, "category": "refund", "content": "申请退款", "idempotency_key": key}
        first = client.post("/api/v1/tickets", json=payload, headers=headers)
        second = client.post("/api/v1/tickets", json=payload, headers=headers)
        assert first.status_code == second.status_code == 200
        ticket_id = first.json()["data"]["ticket_id"]
        assert second.json()["data"]["ticket_id"] == ticket_id
        assert client.put(f"/api/v1/tickets/{ticket_id}", json={"status": "closed"}, headers=_login(client, "admin", "admin12345")).status_code == 409
        assert client.put(f"/api/v1/tickets/{ticket_id}", json={"status": "processing"}, headers=_login(client, "admin", "admin12345")).status_code == 200
        assert client.get(f"/api/v1/tickets/{ticket_id}", headers=headers).json()["data"]["status"] == "processing"

    # 新请求上下文重新读取，证明不是 TicketManager 进程内字典。
    with TestClient(app) as client:
        assert client.get(f"/api/v1/tickets/{ticket_id}", headers=_login(client)).status_code == 200


def test_handoff_feedback_and_chat_evaluation_are_stored():
    with TestClient(app) as client:
        headers = _login(client)
        session = client.post("/api/v1/chat/sessions?user_id=1", headers=headers).json()["data"]["id"]
        handoff = client.post("/api/v1/handoff", json={"user_id": 1, "session_id": session, "reason": "用户请求", "idempotency_key": "handoff-1"}, headers=headers)
        assert handoff.status_code == 200
        assert client.post("/api/v1/handoff", json={"user_id": 1, "session_id": session, "reason": "用户请求", "idempotency_key": "handoff-1"}, headers=headers).json()["data"]["request_id"] == handoff.json()["data"]["request_id"]
        chat = client.post("/api/v1/chat/send", json={"user_id": 1, "session_id": session, "message": "请查询订单"}, headers=headers)
        assert chat.status_code == 200
        feedback = client.post("/api/v1/feedback/submit", json={"user_id": 1, "session_id": session, "feedback_type": "csat", "score": 5}, headers=headers)
        assert feedback.status_code == 200

    async def verify_rows():
        async with async_session() as db:
            assert await db.scalar(select(HandoffRequestRecord).where(HandoffRequestRecord.idempotency_key == "handoff-1"))
            assert await db.scalar(select(NotificationRecord).where(NotificationRecord.event_type == "handoff.created"))
            assert await db.scalar(select(OperationAudit).where(OperationAudit.operation == "request_handoff"))
            assert await db.scalar(select(EvaluationResult).where(EvaluationResult.session_id == session))
    import asyncio
    asyncio.run(verify_rows())
