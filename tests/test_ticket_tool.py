"""OPT-08/10：工单工具必须落库并提供可信、可重放回执。"""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from sqlalchemy import func, select

from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.agent.tools import CreateTicketTool
from app.config.config import settings
from app.models.models import Message, MessageRole, NotificationRecord, OperationAudit, Ticket, User
from tests.test_ticket_idempotency import api_context
from tests.test_session_creation import session_api


def arguments(owner=101, message_id=1):
    return {"user_id": owner, "session_id": f"session-{owner}", "category": "投诉建议",
            "content": "我要投诉，商品外壳破损", "priority": "high",
            "idempotency_key": f"chat-ticket:session-{owner}:{message_id}"}


def capability(payload, message_id=1):
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return jwt.encode({"purpose": "create_ticket", "sub": str(payload["user_id"]), "message_id": message_id, "actor_id": payload["user_id"],
                       "request_hash": digest, "exp": datetime.now(timezone.utc) + timedelta(minutes=2)},
                      settings.AUTH_SECRET_KEY, algorithm="HS256")


async def seed_message(factory, owner=101, message_id=1):
    async with factory() as db:
        db.add(Message(id=message_id, session_id=f"session-{owner}", role=MessageRole.USER, content="合成投诉"))
        await db.commit()


async def counts(factory):
    async with factory() as db:
        return [await db.scalar(select(func.count()).select_from(model))
                for model in (Ticket, NotificationRecord, OperationAudit)]


async def test_success_is_persistent_and_visible_through_api(tmp_path, monkeypatch):
    async with api_context(tmp_path / "tool.db") as (client, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed_message(factory)
        payload = arguments()
        result = await CreateTicketTool().execute(**payload, authorization=capability(payload))
        assert result["success"] is True
        assert await counts(factory) == [1, 1, 1]
        ticket_id = result["data"]["ticket_id"]
        detail = await client.get(f"/api/v1/tickets/{ticket_id}")
        assert detail.status_code == 200
        assert detail.json()["data"]["user_id"] == 101
        assert result["data"]["assigned_to"] is None and result["data"]["sla_deadline"] is None
        assert "已分配" not in result["data"]["message"] and "小时" not in result["data"]["message"]
        # 新工具实例与新DB会话模拟进程缓存丢失，不可再生成新资源。
        replay = await CreateTicketTool().execute(**payload, authorization=capability(payload))
        assert replay["data"]["ticket_id"] == ticket_id
        assert await counts(factory) == [1, 1, 1]
        # API与工具同字段同键重放同一提交。
        api_replay = await client.post("/api/v1/tickets", json=payload)
        assert api_replay.status_code == 200
        assert api_replay.json()["data"]["ticket_id"] == ticket_id


@pytest.mark.parametrize("change", ["missing_auth", "owner", "content", "session", "bool_user", "extra"])
async def test_untrusted_or_changed_arguments_do_not_write(tmp_path, monkeypatch, change):
    async with api_context(tmp_path / "tool.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed_message(factory)
        payload = arguments()
        authorization = capability(payload)
        if change == "missing_auth":
            authorization = ""
        elif change == "owner":
            payload["user_id"] = 202
        elif change == "content":
            payload["content"] = "换一个请求"
        elif change == "session":
            payload["session_id"] = "session-202"
        elif change == "bool_user":
            payload["user_id"] = True
        else:
            payload["actor_id"] = 999
        result = await CreateTicketTool().execute(**payload, authorization=authorization)
        assert result["success"] is False and not result.get("data")
        assert await counts(factory) == [0, 0, 0]


async def test_database_failure_never_returns_fake_ticket(monkeypatch):
    def unavailable():
        raise RuntimeError("synthetic-private-connection")
    monkeypatch.setattr("app.utils.database.async_session", unavailable)
    payload = arguments()
    result = await CreateTicketTool().execute_with_retry(**payload, authorization=capability(payload))
    assert result.success is False and result.data is None
    assert result.retry_count == 0
    assert "synthetic-private-connection" not in str(result)


async def test_same_key_changed_payload_does_not_hit_process_cache(tmp_path, monkeypatch):
    async with api_context(tmp_path / "tool.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed_message(factory)
        tool = CreateTicketTool()
        payload = arguments()
        assert (await tool.execute_with_retry(**payload, authorization=capability(payload))).success
        payload["content"] = "修改后的投诉"
        changed = await tool.execute_with_retry(**payload, authorization=capability(payload))
        assert changed.success is False and changed.error_type == "conflict"
        assert await counts(factory) == [1, 1, 1]


async def test_tool_node_overrides_untrusted_identity_and_keeps_capability_private(tmp_path, monkeypatch):
    async with api_context(tmp_path / "tool.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        monkeypatch.setattr("app.agent.mcp_client._ENABLED", False)
        await seed_message(factory)
        state = AgentState(session_id="session-101", user_id=101, user_message="我要投诉，商品外壳破损",
                           detected_intent="complaint", source_message_id=1,
                           collected_info={"user_id": 202, "session_id": "session-202"})
        await object.__new__(AgentGraph)._node_tool_execution(state)
        assert state.tool_calls[0].success
        assert await counts(factory) == [1, 1, 1]
        async with factory() as db:
            item = await db.scalar(select(Ticket))
            assert item.user_id == 101 and item.session_id == "session-101"
        assert "authorization" not in state.tool_calls[0].arguments
        assert state.for_next_turn(101, "下一轮").source_message_id is None


@pytest.mark.parametrize("stage", ["before", "after"])
async def test_commit_failure_or_lost_receipt_is_replayed_safely(tmp_path, monkeypatch, stage):
    from sqlalchemy.ext.asyncio import AsyncSession
    async with api_context(tmp_path / "tool.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed_message(factory)
        original = AsyncSession.commit

        async def fail(db):
            if stage == "after":
                await original(db)
            else:
                await db.flush()
            raise RuntimeError("synthetic-private-commit-error")

        payload = arguments()
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "commit", fail)
            result = await CreateTicketTool().execute_with_retry(**payload, authorization=capability(payload))
            assert not result.success and result.data is None and result.retry_count == 0
            assert "synthetic-private" not in str(result)
        assert await counts(factory) == ([1, 1, 1] if stage == "after" else [0, 0, 0])
        replay = await CreateTicketTool().execute(**payload, authorization=capability(payload))
        assert replay["success"] and await counts(factory) == [1, 1, 1]


@pytest.mark.parametrize("source", ["missing", "other_session", "assistant", "disabled"])
async def test_invalid_source_or_disabled_actor_cannot_create(tmp_path, monkeypatch, source):
    async with api_context(tmp_path / "tool.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        if source != "missing":
            await seed_message(factory)
        async with factory() as db:
            if source in {"other_session", "assistant"}:
                item = await db.get(Message, 1)
                if source == "other_session":
                    item.session_id = "session-202"
                else:
                    item.role = MessageRole.ASSISTANT
            elif source == "disabled":
                (await db.get(User, 101)).status = False
            await db.commit()
        payload = arguments()
        result = await CreateTicketTool().execute(**payload, authorization=capability(payload))
        assert not result["success"] and await counts(factory) == [0, 0, 0]


async def test_inprocess_mcp_uses_same_persistent_receipt(tmp_path, monkeypatch):
    from mcp.shared.memory import create_connected_server_and_client_session
    from app.agent.mcp_server import mcp
    from app.agent.mcp_client import _extract_text_content
    async with api_context(tmp_path / "tool.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed_message(factory)
        payload = arguments()
        async with create_connected_server_and_client_session(mcp._mcp_server) as client:
            await client.initialize()
            raw = await client.call_tool("create_ticket", {**payload, "authorization": capability(payload)})
            result = json.loads(_extract_text_content(raw))
            assert result["success"] is True
            second = await client.call_tool("create_ticket", {**payload, "authorization": capability(payload)})
            assert json.loads(_extract_text_content(second))["data"]["ticket_id"] == result["data"]["ticket_id"]
        assert await counts(factory) == [1, 1, 1]


async def test_mcp_lost_response_fallback_does_not_duplicate(tmp_path, monkeypatch):
    import app.agent.mcp_client as client
    async with api_context(tmp_path / "tool.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed_message(factory)
        payload = arguments()

        async def commit_then_lose_response(name, args):
            assert (await CreateTicketTool().execute(**args))["success"]
            return None

        monkeypatch.setattr(client, "_ENABLED", True)
        monkeypatch.setattr(client, "_get_client", lambda: SimpleNamespace(call=commit_then_lose_response))
        result = await client.invoke_tool("create_ticket", **payload, authorization=capability(payload))
        assert result.success and await counts(factory) == [1, 1, 1]


async def test_concurrent_tool_requests_have_one_record(tmp_path, monkeypatch):
    async with api_context(tmp_path / "tool.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed_message(factory)
        payload = arguments()
        results = await asyncio.gather(*[
            CreateTicketTool().execute(**payload, authorization=capability(payload)) for _ in range(10)
        ])
        assert all(item["success"] for item in results)
        assert len({item["data"]["ticket_id"] for item in results}) == 1
        assert await counts(factory) == [1, 1, 1]


@pytest.mark.parametrize("invalid", ["expired", "wrong_purpose", "wrong_message"])
async def test_capability_must_be_current_and_bound_to_source(tmp_path, monkeypatch, invalid):
    async with api_context(tmp_path / "tool.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed_message(factory)
        payload = arguments()
        claims = jwt.decode(capability(payload), settings.AUTH_SECRET_KEY, algorithms=["HS256"])
        if invalid == "expired":
            claims["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif invalid == "wrong_purpose":
            claims["purpose"] = "access"
        else:
            claims["message_id"] = 99
        token = jwt.encode(claims, settings.AUTH_SECRET_KEY, algorithm="HS256")
        result = await CreateTicketTool().execute(**payload, authorization=token)
        assert not result["success"] and await counts(factory) == [0, 0, 0]


async def test_unpersisted_turn_cannot_authorize_ticket(tmp_path, monkeypatch):
    async with api_context(tmp_path / "tool.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        state = AgentState(session_id="session-101", user_id=101, user_message="我要投诉商品破损",
                           detected_intent="complaint", collected_info={"source_message_id": 999})
        await object.__new__(AgentGraph)._node_tool_execution(state)
        assert not state.tool_calls[0].success
        assert "authorization" not in state.tool_calls[0].arguments
        assert await counts(factory) == [0, 0, 0]


@pytest.mark.parametrize("transport,actor", [("rest", 101), ("ws", 101), ("rest", 999)])
def test_chat_real_graph_creates_queryable_ticket(session_api, monkeypatch, transport, actor):
    from langgraph.checkpoint.memory import InMemorySaver
    from app.routers.tickets import router
    import app.routers.chat as chat
    from app.security import create_access_token
    from tests.test_chat_turns import Validator
    from tests.test_chat_auth import receive_turn

    client, factory, manager, _, users = session_api
    client.app.include_router(router)
    graph = AgentGraph()
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    graph.validator = Validator()
    # 冻结理解与审查输入，只测实际编排/工具/事务/发布，不冒充模型质量验收。
    monkeypatch.setattr(graph.intent_recognizer, "recognize", lambda **kw: SimpleNamespace(
        intent="complaint", confidence=1.0, needs_clarification=False, needs_llm_fallback=False))
    monkeypatch.setattr(chat.chat_service, "process", graph.run)
    monkeypatch.setattr(chat.chat_service, "process_stream", graph.run_stream)
    monkeypatch.setattr("app.agent.mcp_client._ENABLED", False)
    session_id = client.post("/api/v1/chat/sessions?user_id=101").json()["data"]["id"]
    body = {"user_id": 101, "session_id": session_id, "message": "我要投诉，商品外壳破损"}
    try:
        if transport == "rest":
            response = client.post("/api/v1/chat/send", json=body, headers={"X-Test-Actor": str(actor)})
            assert response.status_code == 200, response.text
            reply = response.json()["reply"]
        else:
            token = create_access_token(users[0])
            with client.websocket_connect(f"/api/v1/chat/stream?access_token={token}") as ws:
                ws.send_json(body)
                events = receive_turn(ws)
                assert events[-1]["type"] == "stream_end", events
                reply = next(event["reply"] for event in events if event["type"] == "done")
        assert asyncio.run(counts(factory)) == [1, 1, 2]  # 显式建会话审计+工单审计

        async def inspect():
            async with factory() as db:
                item = await db.scalar(select(Ticket))
                audit = await db.scalar(select(OperationAudit).where(OperationAudit.operation == "create_ticket"))
                assert audit.request_payload["actor_id"] == actor
                assert audit.request_payload["owner_id"] == 101
                return item.id, item.user_id, item.session_id

        ticket_id, owner, ticket_session = asyncio.run(inspect())
        assert ticket_id in reply and owner == 101 and ticket_session == session_id
        assert "已分配" not in reply and "小时" not in reply
        assert client.get(f"/api/v1/tickets/{ticket_id}").status_code == 200
        assert client.get("/api/v1/tickets").json()["data"]["total"] == 1
        assert client.get(f"/api/v1/tickets/{ticket_id}", headers={"X-Test-Actor": "999"}).status_code == 200
        state = asyncio.run(manager.get_state(session_id))
        assert "authorization" not in str([call.arguments for call in state.tool_calls])
    finally:
        asyncio.run(graph.llm_service.close())
