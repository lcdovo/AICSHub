"""AC-03-05：真实JWT/ASGI/临时SQLite，图执行使用合成替身。"""
import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import func, select

from app.models.models import AgentTrace, EvaluationResult, Message, Session, SessionStatus
from app.agent.state import AgentState
from app.security import create_access_token
from tests.test_chat_auth import auth_api, headers, receive_turn


def include_session_routes(client):
    from app.routers.sessions import router
    client.app.include_router(router)


def close_path(endpoint, session_id="sa"):
    if endpoint == "sessions":
        return "PUT", f"/api/v1/sessions/{session_id}/close"
    return "PATCH", f"/api/v1/chat/sessions/{session_id}/close"


def inspect_closed(factory, session_id="sa"):
    async def inspect():
        async with factory() as db:
            session = await db.get(Session, session_id)
            counts = [await db.scalar(select(func.count()).select_from(model))
                      for model in (Message, AgentTrace, EvaluationResult)]
            return session.status, session.closed_at, session.message_count, counts
    return asyncio.run(inspect())


def send(client, transport, token, session_id="sa"):
    body = {"user_id": 101, "session_id": session_id, "message": "不应恢复关闭会话"}
    if transport == "rest":
        response = client.post("/api/v1/chat/send", headers=headers(token), json=body)
        return response.status_code, response.json().get("detail"), []
    with client.websocket_connect("/api/v1/chat/stream", headers=headers(token)) as ws:
        ws.send_json(body)
        events = receive_turn(ws)
    return events[-1].get("code"), events[-1].get("message"), events


@pytest.mark.parametrize("endpoint", ["sessions", "chat"])
@pytest.mark.parametrize("transport", ["rest", "ws"])
def test_both_close_endpoints_reject_rest_and_ws_without_writes(auth_api, endpoint, transport):
    client, factory, manager, users, calls = auth_api
    include_session_routes(client)
    token = create_access_token(users[0])
    saved = AgentState(session_id="sa", user_id=101, user_message="合成历史",
                       messages=[{"role": "user", "content": "合成历史"}],
                       pending_confirmations=["apply_refund"])
    asyncio.run(manager.save_state(saved))
    method, path = close_path(endpoint)
    assert client.request(method, path, headers=headers(token)).status_code == 200
    before = inspect_closed(factory)
    assert before[0] == SessionStatus.CLOSED and before[1] is not None
    for _ in range(2):
        code, message, events = send(client, transport, token)
        assert code == 400 and message == "会话已关闭"
        assert not events or [event["type"] for event in events] == ["error"]
        assert inspect_closed(factory) == before
        assert asyncio.run(manager.get_state("sa")).model_dump() == saved.model_dump()
    assert calls == []


@pytest.mark.parametrize("endpoint", ["sessions", "chat"])
@pytest.mark.parametrize("transport", ["rest", "ws"])
def test_close_while_waiting_for_lock_blocks_turn(auth_api, monkeypatch, endpoint, transport):
    client, factory, manager, users, calls = auth_api
    include_session_routes(client)
    token = create_access_token(users[0])
    original_lock = manager.session_lock
    method, path = close_path(endpoint)

    @asynccontextmanager
    async def close_before_acquisition(session_id):
        # 准入读取已结束；在取得真实本地锁前通过实际HTTP关闭入口提交关闭。
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url="http://test") as http:
            result = await http.request(method, path, headers=headers(token))
            assert result.status_code == 200
        async with original_lock(session_id) as assert_owner:
            yield assert_owner

    monkeypatch.setattr(manager, "session_lock", close_before_acquisition)
    code, message, events = send(client, transport, token)
    assert code == 400 and message == "会话已关闭"
    assert not events or [event["type"] for event in events] == ["error"]
    status, closed_at, count, rows = inspect_closed(factory)
    assert status == SessionStatus.CLOSED and closed_at is not None
    assert count == 0 and rows == [0, 0, 0] and calls == []
    assert manager._turn_locks == {}


@pytest.mark.parametrize("transport", ["rest", "ws"])
def test_foreign_closed_session_is_permission_error(auth_api, transport):
    client, factory, _, users, calls = auth_api
    token = create_access_token(users[0])
    b_token = create_access_token(users[1])
    assert client.patch("/api/v1/chat/sessions/sb/close", headers=headers(b_token)).status_code == 200
    code, _, _ = send(client, transport, token, session_id="sb")
    assert code == 403
    assert calls == []
    assert inspect_closed(factory, "sb")[0] == SessionStatus.CLOSED


def test_closed_session_rejection_keeps_ws_usable_for_new_session(auth_api):
    client, factory, _, users, calls = auth_api
    token = create_access_token(users[0])
    assert client.patch("/api/v1/chat/sessions/sa/close", headers=headers(token)).status_code == 200
    before = inspect_closed(factory)
    created = client.post("/api/v1/chat/sessions?user_id=101", headers=headers(token))
    assert created.status_code == 200
    new_session_id = created.json()["data"]["id"]
    with client.websocket_connect("/api/v1/chat/stream", headers=headers(token)) as ws:
        ws.send_json({"user_id": 101, "session_id": "sa", "message": "旧会话"})
        rejected = receive_turn(ws)
        assert rejected[0]["type"] == "error" and rejected[0]["code"] == 400
        ws.send_json({"user_id": 101, "session_id": new_session_id, "message": "新会话"})
        assert receive_turn(ws)[-1]["type"] == "stream_end"
    assert calls == [101]
    # 新会话有消息，旧会话状态、时间和计数均未改变。
    after = inspect_closed(factory)
    assert after[:3] == before[:3]
    assert after[3][0] == 2
