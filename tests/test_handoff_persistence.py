"""OPT-08/10：人工请求须持久，失败不能改写为接通成功。"""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from types import SimpleNamespace

import jwt
import pytest
from sqlalchemy import func, select

from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.agent.tools import EscalateToHumanTool, ToolExecutionResult
from app.config.config import settings
from app.models.models import HandoffRequestRecord, Message, MessageRole, NotificationRecord, OperationAudit, User
from tests.test_ticket_idempotency import api_context
from tests.test_session_creation import session_api
from tests.test_chat_auth import auth_api


def args():
    return {"user_id": 101, "session_id": "session-101", "reason": "用户要求转人工",
            "priority": "normal", "context": "", "idempotency_key": "chat-handoff:session-101:1"}


def capability(payload, actor=101):
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return jwt.encode({"purpose": "request_handoff", "sub": str(payload["user_id"]), "actor_id": actor,
                       "message_id": 1, "request_hash": digest,
                       "exp": datetime.now(timezone.utc) + timedelta(minutes=2)}, settings.AUTH_SECRET_KEY, algorithm="HS256")


async def seed(factory):
    async with factory() as db:
        db.add(Message(id=1, session_id="session-101", role=MessageRole.USER, content="合成转人工请求"))
        await db.commit()


async def counts(factory):
    async with factory() as db:
        return [await db.scalar(select(func.count()).select_from(model))
                for model in (HandoffRequestRecord, NotificationRecord, OperationAudit)]


async def test_handoff_is_persistent_and_queryable_by_owner_and_admin(tmp_path, monkeypatch):
    async with api_context(tmp_path / "handoff.db") as (client, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        payload = args()
        result = await EscalateToHumanTool().execute(**payload, authorization=capability(payload))
        assert result["success"] is True
        assert await counts(factory) == [1, 1, 1]
        data = result["data"]
        assert data["request_id"] == data["escalation_id"]
        assert data["status"] == "pending" and data["assigned_to"] is None
        assert "预计等待" not in data["message"] and "已接通" not in data["message"]
        assert "queue_position" not in data and "estimated_wait_time" not in data
        owner = await client.get(f"/api/v1/handoff/requests/{data['request_id']}")
        assert owner.status_code == 200 and owner.json()["data"]["user_id"] == 101
        assert (await client.get(f"/api/v1/handoff/requests/{data['request_id']}", headers={"X-Test-Actor": "202"})).status_code == 403
        admin = await client.get("/api/v1/handoff/requests", headers={"X-Test-Actor": "999"})
        assert admin.json()["data"]["requests"][0]["request_id"] == data["request_id"]
        replay = await EscalateToHumanTool().execute(**payload, authorization=capability(payload))
        assert replay["data"]["request_id"] == data["request_id"] and await counts(factory) == [1, 1, 1]
        api_replay = await client.post("/api/v1/handoff", json={**payload, "context": {}})
        assert api_replay.status_code == 200 and api_replay.json()["data"]["request_id"] == data["request_id"]


async def test_database_error_never_fabricates_queue(monkeypatch):
    def unavailable():
        raise RuntimeError("synthetic-private-db")
    monkeypatch.setattr("app.utils.database.async_session", unavailable)
    payload = args()
    result = await EscalateToHumanTool().execute_with_retry(**payload, authorization=capability(payload))
    assert not result.success and result.data is None and result.retry_count == 0
    assert "synthetic-private-db" not in str(result)


@pytest.mark.parametrize("change", ["missing", "owner", "session", "reason", "bool_user", "extra"])
async def test_untrusted_arguments_never_create_handoff(tmp_path, monkeypatch, change):
    async with api_context(tmp_path / "handoff.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        payload = args()
        token = capability(payload)
        if change == "missing":
            token = ""
        elif change == "owner":
            payload["user_id"] = 202
        elif change == "session":
            payload["session_id"] = "session-202"
        elif change == "reason":
            payload["reason"] = "不同请求"
        elif change == "bool_user":
            payload["user_id"] = True
        else:
            payload["actor_id"] = 999
        result = await EscalateToHumanTool().execute(**payload, authorization=token)
        assert not result["success"] and not result.get("data")
        assert await counts(factory) == [0, 0, 0]


@pytest.mark.parametrize("outcome", ["missing", "failure", "malformed", "bad_status"])
async def test_handoff_node_never_replaces_failure_with_success(monkeypatch, outcome):
    result = {"missing": None,
              "failure": ToolExecutionResult(success=False, error="服务不可用", data=None),
              "malformed": ToolExecutionResult(success=True, data={"message": "已接通人工，预计等待3分钟"}),
              "bad_status": ToolExecutionResult(success=True, data={"request_id": "invalid", "status": [],
                  "user_id": 101, "session_id": "session-101"})}[outcome]
    monkeypatch.setattr("app.agent.nodes.handoff_node.invoke_tool", AsyncMock(return_value=result))
    state = AgentState(session_id="session-101", user_id=101, user_message="转人工", source_message_id=1)
    await object.__new__(AgentGraph)._node_human_handoff(state)
    assert state.collected_info["handoff_attempted"]
    assert state.tool_calls and not state.tool_calls[-1].success
    assert "预计等待" not in state.reply and "已接通" not in state.reply and "已为您转接" not in state.reply
    assert "authorization" not in str([call.arguments for call in state.tool_calls])


def test_reply_template_does_not_invent_wait_time():
    reply = object.__new__(AgentGraph)._build_escalate_reply_template({"request_id": "persisted-id", "status": "pending"})
    assert "persisted-id" in reply and "预计等待" not in reply


def test_handoff_detail_uses_real_authentication(auth_api):
    from app.routers.tickets import router
    from app.security import create_access_token
    client, factory, _, users, _ = auth_api
    client.app.include_router(router)

    async def setup():
        async with factory() as db:
            db.add(HandoffRequestRecord(id="synthetic-handoff", user_id=101, session_id="sa",
                                        reason="合成人工请求", priority="normal"))
            await db.commit()

    asyncio.run(setup())
    path = "/api/v1/handoff/requests/synthetic-handoff"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": f"Bearer {create_access_token(users[0])}"}).status_code == 200
    assert client.get(path, headers={"Authorization": f"Bearer {create_access_token(users[1])}"}).status_code == 403


@pytest.mark.parametrize("stage", ["before", "after"])
async def test_commit_fault_never_fakes_success_and_replay_is_unique(tmp_path, monkeypatch, stage):
    from sqlalchemy.ext.asyncio import AsyncSession
    async with api_context(tmp_path / "handoff.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        original = AsyncSession.commit

        async def fail(db):
            if stage == "after":
                await original(db)
            else:
                await db.flush()
            raise RuntimeError("synthetic-private-commit")

        payload = args()
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "commit", fail)
            result = await EscalateToHumanTool().execute_with_retry(**payload, authorization=capability(payload))
            assert not result.success and result.data is None and result.retry_count == 0
            assert "synthetic-private-commit" not in str(result)
        assert await counts(factory) == ([1, 1, 1] if stage == "after" else [0, 0, 0])
        replay = await EscalateToHumanTool().execute(**payload, authorization=capability(payload))
        assert replay["success"] and await counts(factory) == [1, 1, 1]


async def test_same_key_changed_reason_is_conflict_without_process_cache(tmp_path, monkeypatch):
    async with api_context(tmp_path / "handoff.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        tool = EscalateToHumanTool()
        payload = args()
        assert (await tool.execute_with_retry(**payload, authorization=capability(payload))).success
        payload["reason"] = "修改原因"
        result = await tool.execute_with_retry(**payload, authorization=capability(payload))
        assert not result.success and result.error_type == "conflict"
        assert await counts(factory) == [1, 1, 1]


@pytest.mark.parametrize("invalid", ["expired", "purpose", "source", "disabled"])
async def test_purpose_expiry_source_and_current_actor_checked(tmp_path, monkeypatch, invalid):
    async with api_context(tmp_path / "handoff.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        payload = args()
        claims = jwt.decode(capability(payload), settings.AUTH_SECRET_KEY, algorithms=["HS256"])
        if invalid == "expired":
            claims["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif invalid == "purpose":
            claims["purpose"] = "create_ticket"
        elif invalid == "source":
            claims["message_id"] = 99
        else:
            async with factory() as db:
                (await db.get(User, 101)).status = False
                await db.commit()
        token = jwt.encode(claims, settings.AUTH_SECRET_KEY, algorithm="HS256")
        result = await EscalateToHumanTool().execute(**payload, authorization=token)
        assert not result["success"] and await counts(factory) == [0, 0, 0]


async def test_ten_concurrent_calls_only_create_one_request(tmp_path, monkeypatch):
    async with api_context(tmp_path / "handoff.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        payload = args()
        results = await asyncio.gather(*[EscalateToHumanTool().execute(**payload, authorization=capability(payload)) for _ in range(10)])
        assert all(item["success"] for item in results)
        assert len({item["data"]["request_id"] for item in results}) == 1
        assert await counts(factory) == [1, 1, 1]


async def test_inprocess_mcp_returns_persistent_handoff(tmp_path, monkeypatch):
    from mcp.shared.memory import create_connected_server_and_client_session
    from app.agent.mcp_server import mcp
    from app.agent.mcp_client import _extract_text_content
    from app.agent.handoff_authorization import authorize_handoff
    async with api_context(tmp_path / "handoff.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        payload = args()
        async with create_connected_server_and_client_session(mcp._mcp_server) as client:
            await client.initialize()
            raw = await client.call_tool("escalate_to_human", {**payload, "authorization": authorize_handoff(payload, 1, 101)})
            result = json.loads(_extract_text_content(raw))
            assert result["success"] and result["data"]["status"] == "pending"
            assert "预计等待" not in result["data"]["message"]
        assert await counts(factory) == [1, 1, 1]


@pytest.mark.parametrize("transport,actor", [("rest", 101), ("ws", 101), ("rest", 999)])
def test_real_chat_graph_handoff_matches_database(session_api, monkeypatch, transport, actor):
    from langgraph.checkpoint.memory import InMemorySaver
    from app.routers.tickets import router
    import app.routers.chat as chat
    from app.security import create_access_token
    from tests.test_chat_auth import receive_turn

    client, factory, manager, _, users = session_api
    client.app.include_router(router)
    graph = AgentGraph()
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    monkeypatch.setattr(graph.intent_recognizer, "recognize", lambda **kw: SimpleNamespace(
        intent="human", confidence=1.0, needs_clarification=False, needs_llm_fallback=False))
    monkeypatch.setattr(chat.chat_service, "process", graph.run)
    monkeypatch.setattr(chat.chat_service, "process_stream", graph.run_stream)
    monkeypatch.setattr("app.agent.mcp_client._ENABLED", False)
    session_id = client.post("/api/v1/chat/sessions?user_id=101").json()["data"]["id"]
    body = {"user_id": 101, "session_id": session_id, "message": "我要转人工"}
    try:
        if transport == "rest":
            result = client.post("/api/v1/chat/send", json=body, headers={"X-Test-Actor": str(actor)})
            assert result.status_code == 200, result.text
            reply = result.json()["reply"]
        else:
            token = create_access_token(users[0])
            with client.websocket_connect(f"/api/v1/chat/stream?access_token={token}") as ws:
                ws.send_json(body)
                events = receive_turn(ws)
                assert events[-1]["type"] == "stream_end", events
                reply = next(event["reply"] for event in events if event["type"] == "done")
        assert asyncio.run(counts(factory)) == [1, 1, 2]

        async def inspect():
            async with factory() as db:
                item = await db.scalar(select(HandoffRequestRecord))
                audit = await db.scalar(select(OperationAudit).where(OperationAudit.operation == "request_handoff"))
                assert audit.request_payload["actor_id"] == actor and audit.user_id == 101
                return item.id, item.session_id

        request_id, owner_session = asyncio.run(inspect())
        assert request_id in reply and owner_session == session_id
        assert "预计等待" not in reply and "已为您转接" not in reply and "已接通" not in reply
        assert client.get(f"/api/v1/handoff/requests/{request_id}").json()["data"]["status"] == "pending"
        assert client.get("/api/v1/handoff/requests", headers={"X-Test-Actor": "999"}).json()["data"]["total"] == 1
    finally:
        asyncio.run(graph.llm_service.close())
