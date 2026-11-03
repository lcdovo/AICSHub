"""OPT-08/09：仅登记模拟申请，不能凭随机ID宣称已提交。"""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import select

from app.agent.tools import ApplyRefundTool
from app.config.config import settings
from app.models.models import Message, MessageRole, OperationAudit, Order, RefundApplication
from tests.test_ticket_idempotency import api_context
from tests.test_session_creation import session_api


def args(refund_type="refund"):
    return {"order_id": "ORD900001", "reason": "合成商品外壳破损", "type": refund_type,
            "user_id": 101, "session_id": "session-101", "idempotency_key": "synthetic-refund-k"}


def capability(payload):
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return jwt.encode({"purpose": "apply_refund", "sub": str(payload["user_id"]), "actor_id": 101,
                       "message_id": 1, "request_hash": digest, "exp": datetime.now(timezone.utc) + timedelta(minutes=2)},
                      settings.AUTH_SECRET_KEY, algorithm="HS256")


async def seed(factory, owner=101, status="shipped", confirmation="确认"):
    async with factory() as db:
        db.add(Message(id=1, session_id="session-101", role=MessageRole.USER, content=confirmation))
        db.add(Order(order_id="ORD900001", user_id=owner, status=status, status_code=status,
                     total_amount=99.10, items=[]))
        await db.commit()


async def prepared(factory, payload):
    from app.services.refund_actions import prepare
    action = await prepare(factory, 101, payload)
    return {**payload, "action_id": action["action_id"], "action_version": action["version"]}


@pytest.mark.parametrize("refund_type", ["refund", "return", "exchange"])
async def test_confirmed_application_has_persistent_audit_and_no_payment_promises(tmp_path, monkeypatch, refund_type):
    async with api_context(tmp_path / "refund.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        payload = await prepared(factory, args(refund_type))
        result = await ApplyRefundTool().execute(**payload, authorization=capability(payload))
        assert result["success"] is True
        data = result["data"]
        assert data["is_simulated"] is True and data["status"] == "pending_review"
        assert str(data["amount"]) == "99.10"
        assert "shipping_address" not in data
        assert "短信" not in str(data) and "工作日" not in str(data)
        async with factory() as db:
            audit = await db.scalar(select(OperationAudit).where(OperationAudit.operation == "apply_refund"))
            assert audit is not None and audit.resource_id == data["refund_id"]
        replay = await ApplyRefundTool().execute(**payload, authorization=capability(payload))
        assert replay["success"] and replay["data"] == data


@pytest.mark.parametrize("case", ["no_auth", "foreign_order", "no_confirmation", "invalid_state", "tamper",
                                  "bool_owner", "extra", "expired"])
async def test_refund_submission_requires_owner_confirmation_and_bound_arguments(tmp_path, monkeypatch, case):
    async with api_context(tmp_path / "refund.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory, owner=202 if case == "foreign_order" else 101,
                   status="cancelled" if case == "invalid_state" else "shipped",
                   confirmation="谢谢" if case == "no_confirmation" else "确认")
        payload = args()
        token = "" if case == "no_auth" else capability(payload)
        if case == "tamper":
            payload["reason"] = "修改原因"
        elif case == "bool_owner":
            payload["user_id"] = True
        elif case == "extra":
            payload["confirm_refund"] = True
        elif case == "expired":
            claims = jwt.decode(token, settings.AUTH_SECRET_KEY, algorithms=["HS256"])
            claims["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
            token = jwt.encode(claims, settings.AUTH_SECRET_KEY, algorithm="HS256")
        result = await ApplyRefundTool().execute(**payload, authorization=token)
        assert not result["success"] and not result.get("data")
        async with factory() as db:
            assert await db.scalar(select(OperationAudit).where(OperationAudit.operation == "apply_refund")) is None


async def test_same_key_changed_parameters_conflict_and_ten_replays_are_unique(tmp_path, monkeypatch):
    from sqlalchemy import func
    async with api_context(tmp_path / "refund.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        payload = await prepared(factory, args())
        results = await asyncio.gather(*[ApplyRefundTool().execute(**payload, authorization=capability(payload)) for _ in range(10)])
        assert all(item["success"] for item in results)
        assert len({item["data"]["refund_id"] for item in results}) == 1
        payload["reason"] = "另一个原因"
        result = await ApplyRefundTool().execute_with_retry(**payload, authorization=capability(payload))
        assert not result.success and result.error_type == "conflict"
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(RefundApplication)) == 1
            assert await db.scalar(select(func.count()).select_from(OperationAudit).where(OperationAudit.operation == "apply_refund")) == 1


@pytest.mark.parametrize("stage", ["before", "after"])
async def test_commit_fault_never_fakes_success_and_original_request_replays(tmp_path, monkeypatch, stage):
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy import func
    async with api_context(tmp_path / "refund.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        original = AsyncSession.commit

        async def fail(db):
            if stage == "after":
                await original(db)
            else:
                await db.flush()
            raise RuntimeError("synthetic-private-submit")

        payload = await prepared(factory, args())
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "commit", fail)
            result = await ApplyRefundTool().execute_with_retry(**payload, authorization=capability(payload))
            assert not result.success and result.data is None and result.retry_count == 0
            assert "synthetic-private-submit" not in str(result)
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(RefundApplication)) == (1 if stage == "after" else 0)
        replay = await ApplyRefundTool().execute(**payload, authorization=capability(payload))
        assert replay["success"]
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(RefundApplication)) == 1
            assert await db.scalar(select(func.count()).select_from(OperationAudit).where(OperationAudit.operation == "apply_refund")) == 1


async def test_refund_query_uses_real_auth_and_owner_scope(tmp_path, monkeypatch):
    from app.routers.refunds import router
    from app.security import create_access_token, get_current_user
    from app.models.models import User
    import httpx
    from fastapi import FastAPI
    from app.utils.database import get_db

    async with api_context(tmp_path / "refund.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        payload = await prepared(factory, args())
        result = await ApplyRefundTool().execute(**payload, authorization=capability(payload))
        path = f"/api/v1/refunds/{result['data']['refund_id']}"
        app = FastAPI()
        app.include_router(router)

        async def db_override():
            async with factory() as db:
                yield db

        app.dependency_overrides[get_db] = db_override
        async with factory() as db:
            tokens = {owner: create_access_token(await db.get(User, owner)) for owner in (101, 202, 999)}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(path)).status_code == 401
            for owner, code in ((101, 200), (202, 403), (999, 200)):
                response = await client.get(path, headers={"Authorization": f"Bearer {tokens[owner]}"})
                assert response.status_code == code
                if code == 200:
                    assert response.json()["data"] == result["data"]


async def test_inprocess_mcp_persists_same_simulated_application(tmp_path, monkeypatch):
    from mcp.shared.memory import create_connected_server_and_client_session
    from app.agent.mcp_server import mcp
    from app.agent.mcp_client import _extract_text_content
    from app.agent.refund_authorization import authorize_refund
    async with api_context(tmp_path / "refund.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        payload = await prepared(factory, args())
        async with create_connected_server_and_client_session(mcp._mcp_server) as client:
            await client.initialize()
            raw = await client.call_tool("apply_refund", {**payload, "authorization": authorize_refund(payload, 1, 101)})
            result = json.loads(_extract_text_content(raw))
            assert result["success"] and result["data"]["is_simulated"]
        async with factory() as db:
            assert (await db.get(RefundApplication, result["data"]["refund_id"])).status == "pending_review"


@pytest.mark.parametrize("transport", ["rest", "ws"])
def test_real_graph_only_persists_after_current_confirmation(session_api, monkeypatch, transport):
    from langgraph.checkpoint.memory import InMemorySaver
    from types import SimpleNamespace
    from app.agent.graph import AgentGraph
    from app.routers.refunds import router
    import app.routers.chat as chat
    from app.security import create_access_token
    from tests.test_chat_turns import Validator
    from tests.test_chat_auth import receive_turn
    from sqlalchemy import func

    client, factory, _, _, users = session_api
    client.app.include_router(router)
    graph = AgentGraph()
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    graph.validator = Validator()
    monkeypatch.setattr(graph.intent_recognizer, "recognize", lambda **kw: SimpleNamespace(
        intent="refund", confidence=1.0, needs_clarification=False, needs_llm_fallback=False))
    monkeypatch.setattr(chat.chat_service, "process", graph.run)
    monkeypatch.setattr(chat.chat_service, "process_stream", graph.run_stream)
    monkeypatch.setattr("app.agent.mcp_client._ENABLED", False)

    async def setup():
        async with factory() as db:
            db.add(Order(order_id="ORD900001", user_id=101, status="已发货", status_code="shipped", total_amount=0.10, items=[]))
            await db.commit()

    async def count():
        async with factory() as db:
            return await db.scalar(select(func.count()).select_from(RefundApplication))

    asyncio.run(setup())
    session_id = client.post("/api/v1/chat/sessions?user_id=101").json()["data"]["id"]
    try:
        preview = client.post("/api/v1/chat/send", json={"user_id": 101, "session_id": session_id,
                                                        "message": "ORD900001 我要退款，商品外壳严重破损"})
        assert preview.status_code == 200 and "是否确认" in preview.json()["reply"]
        assert asyncio.run(count()) == 0
        body = {"user_id": 101, "session_id": session_id, "message": "确认"}
        if transport == "rest":
            result = client.post("/api/v1/chat/send", json=body)
            assert result.status_code == 200, result.text
            reply = result.json()["reply"]
        else:
            with client.websocket_connect(f"/api/v1/chat/stream?access_token={create_access_token(users[0])}") as ws:
                ws.send_json(body)
                events = receive_turn(ws)
                assert events[-1]["type"] == "stream_end", events
                reply = next(event["reply"] for event in events if event["type"] == "done")
                assert "authorization" not in str(events)
        assert asyncio.run(count()) == 1
        assert "模拟" in reply and "0.10" in reply and "已到账" not in reply
        async def application_id():
            async with factory() as db:
                return (await db.scalar(select(RefundApplication))).id
        refund_id = asyncio.run(application_id())
        assert refund_id in reply
        assert client.get(f"/api/v1/refunds/{refund_id}").json()["data"]["is_simulated"] is True
    finally:
        asyncio.run(graph.llm_service.close())


def test_additive_refund_migration_preserves_orders(tmp_path):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect
    from app.models.models import Base
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0006_refund_applications.py"
    spec = importlib.util.spec_from_file_location("refund_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    try:
        with engine.begin() as db:
            Base.metadata.create_all(db, tables=[t for t in Base.metadata.sorted_tables if t.name != "refund_applications"])
            db.execute(Order.__table__.insert().values(order_id="ORD900001", user_id=101, status="已发货",
                                                       status_code="shipped", total_amount=99.10, items=[]))
            before = db.execute(select(Order.__table__)).mappings().all()
            with Operations.context(MigrationContext.configure(db)):
                migration.upgrade()
                migration.upgrade()
                assert inspect(db).has_table("refund_applications")
                migration.downgrade()
                assert not inspect(db).has_table("refund_applications")
                migration.upgrade()
                assert db.execute(select(Order.__table__)).mappings().all() == before
    finally:
        engine.dispose()
