"""OPT-04/10：真实 ASGI、临时 SQLite，认证使用合成主体替身。"""
import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import Depends, FastAPI, Header
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.models import (
    Base, HandoffRequestRecord, NotificationRecord, OperationAudit, Session,
    SessionStatus, Ticket, TicketPriority, TicketStatus, User,
)
from app.routers.tickets import router
from app.security import get_current_user
from app.utils.database import get_db


@asynccontextmanager
async def api_context(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}", connect_args={"timeout": 20})

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add_all([User(id=101, username="synthetic-a", role="user"),
                    User(id=202, username="synthetic-b", role="user"),
                    User(id=999, username="synthetic-admin", role="admin")])
        await db.flush()
        db.add_all([Session(id=f"session-{owner}", user_id=owner, status=SessionStatus.ACTIVE)
                    for owner in (101, 202)])
        await db.commit()
    app = FastAPI()
    app.include_router(router)

    async def db_override():
        async with factory() as db:
            yield db

    # 使用每个请求自己的 header 解析合成主体，避免并发测试共享可变认证状态。
    async def auth_override(x_test_actor: int = Header(default=101), db: AsyncSession = Depends(get_db)):
        return await db.get(User, x_test_actor)

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = auth_override
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client, factory
    finally:
        await engine.dispose()


def payload(kind, owner=101):
    if kind == "tickets":
        return {"user_id": owner, "category": "技术支持", "content": "合成问题", "priority": "medium"}
    return {"user_id": owner, "session_id": f"session-{owner}", "reason": "合成原因", "context": {"a": 1}}


async def send(client, kind, body, key="shared-k", actor=101):
    return await client.post(f"/api/v1/{kind}", json=body,
                             headers={"Idempotency-Key": key, "X-Test-Actor": str(actor)})


@pytest.mark.parametrize("kind", ["tickets", "handoff"])
async def test_cross_owner_key_never_returns_foreign_receipt(tmp_path, kind):
    async with api_context(tmp_path / "api.db") as (client, _):
        b = await send(client, kind, payload(kind, 202), actor=202)
        a = await send(client, kind, payload(kind))
        assert b.status_code == 200
        assert a.status_code in {200, 409}
        if a.status_code == 200:
            assert a.json()["data"]["user_id"] == 101
        id_field = "ticket_id" if kind == "tickets" else "request_id"
        assert "session-202" not in a.text and b.json()["data"][id_field] not in a.text


@pytest.mark.parametrize("kind,field,value", [
    ("tickets", "content", "更改描述"), ("tickets", "category", "订单问题"),
    ("tickets", "priority", "high"), ("tickets", "session_id", "session-101"),
    ("handoff", "reason", "更改原因"), ("handoff", "priority", "high"),
    ("handoff", "context", {"a": 2}),
])
async def test_changed_payload_conflicts(tmp_path, kind, field, value):
    async with api_context(tmp_path / "api.db") as (client, _):
        body = payload(kind)
        assert (await send(client, kind, body)).status_code == 200
        body[field] = value
        response = await send(client, kind, body)
        assert response.status_code == 409


@pytest.mark.parametrize("kind", ["tickets", "handoff"])
async def test_replay_is_persistent_and_does_not_duplicate_events(tmp_path, kind):
    async with api_context(tmp_path / "api.db") as (client, factory):
        first = await send(client, kind, payload(kind))
        # 每次HTTP请求均建立新的SQLAlchemy会话；当前状态变化不改变原请求摘要。
        async with factory() as db:
            item = await db.scalar(select(Ticket if kind == "tickets" else HandoffRequestRecord))
            item.priority = TicketPriority.HIGH if kind == "tickets" else "high"
            await db.commit()
        replay = await send(client, kind, payload(kind))
        assert replay.status_code == 200
        id_field = "ticket_id" if kind == "tickets" else "request_id"
        assert replay.json()["data"][id_field] == first.json()["data"][id_field]
        async with factory() as db:
            for model in (Ticket if kind == "tickets" else HandoffRequestRecord, NotificationRecord, OperationAudit):
                assert await db.scalar(select(func.count()).select_from(model)) == 1


async def test_same_key_across_operations_and_actor_is_isolated(tmp_path):
    async with api_context(tmp_path / "api.db") as (client, factory):
        for kind, actor in (("tickets", 101), ("handoff", 101), ("tickets", 999)):
            result = await send(client, kind, payload(kind), actor=actor)
            assert result.status_code == 200
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(OperationAudit)) == 3


@pytest.mark.parametrize("kind", ["tickets", "handoff"])
async def test_foreign_session_and_forged_owner_are_rejected_before_replay(tmp_path, kind):
    async with api_context(tmp_path / "api.db") as (client, _):
        body = payload(kind)
        assert (await send(client, kind, body)).status_code == 200
        body["user_id"] = 202
        assert (await send(client, kind, body)).status_code == 403
        body["user_id"] = 101
        body["session_id"] = "session-202"
        assert (await send(client, kind, body, key="new-k")).status_code == 404


@pytest.mark.parametrize("kind", ["tickets", "handoff"])
async def test_body_and_header_key_mismatch_rejected(tmp_path, kind):
    async with api_context(tmp_path / "api.db") as (client, _):
        body = {**payload(kind), "idempotency_key": "different-k"}
        assert (await send(client, kind, body)).status_code == 400


async def test_legacy_ticket_key_fails_closed_without_duplicate(tmp_path):
    async with api_context(tmp_path / "api.db") as (client, factory):
        async with factory() as db:
            db.add(Ticket(id="legacy-ticket", user_id=101, content="合成问题", category="技术支持",
                          priority=TicketPriority.MEDIUM, status=TicketStatus.PENDING, idempotency_key="legacy-k"))
            await db.commit()
        result = await send(client, "tickets", payload("tickets"), key="legacy-k")
        assert result.status_code == 409
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(Ticket)) == 1


@pytest.mark.parametrize("kind", ["tickets", "handoff"])
@pytest.mark.parametrize("changed", [False, True])
async def test_ten_concurrent_requests_have_one_receipt(tmp_path, monkeypatch, kind, changed):
    import app.services.ticket_creation as service

    original = service._load_receipt
    async with api_context(tmp_path / "race.db") as (client, factory):
        # 同输入每种操作20轮；另测混合参数竞争，不允许悄悄返回另一参数的成功。
        rounds = 1 if changed else 20
        for iteration in range(rounds):
            barrier = asyncio.Barrier(10)

            async def synchronized_read(db, key):
                audit = await original(db, key)
                if key.startswith("ticket-v1:") and audit is None:
                    await asyncio.wait_for(barrier.wait(), timeout=15)
                return audit

            monkeypatch.setattr(service, "_load_receipt", synchronized_read)
            bodies = [payload(kind) for _ in range(10)]
            if changed:
                bodies[-1]["content" if kind == "tickets" else "reason"] = "竞争中的不同请求"
            responses = await asyncio.gather(*[
                send(client, kind, body, key=f"race-{iteration}") for body in bodies
            ])
            assert all(response.status_code in {200, 409} for response in responses)
            if not changed:
                assert all(response.status_code == 200 for response in responses)
            else:
                assert any(response.status_code == 409 for response in responses)
            id_field = "ticket_id" if kind == "tickets" else "request_id"
            receipts = {response.json()["data"][id_field] for response in responses if response.status_code == 200}
            assert len(receipts) == 1
            successful_bodies = [body for body, response in zip(bodies, responses) if response.status_code == 200]
            assert all(body == successful_bodies[0] for body in successful_bodies)
            async with factory() as db:
                for model in (Ticket if kind == "tickets" else HandoffRequestRecord, NotificationRecord, OperationAudit):
                    assert await db.scalar(select(func.count()).select_from(model)) == iteration + 1


@pytest.mark.parametrize("kind", ["tickets", "handoff"])
async def test_commit_failure_rolls_back_resource_audit_and_notification(tmp_path, monkeypatch, kind):
    from sqlalchemy.ext.asyncio import AsyncSession

    async with api_context(tmp_path / "atomic.db") as (client, factory):
        original_commit = AsyncSession.commit

        async def fail_commit(db):
            await db.flush()
            raise RuntimeError("synthetic failure before commit")

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "commit", fail_commit)
            with pytest.raises(RuntimeError, match="synthetic failure"):
                await send(client, kind, payload(kind))
        async with factory() as db:
            for model in (Ticket if kind == "tickets" else HandoffRequestRecord, NotificationRecord, OperationAudit):
                assert await db.scalar(select(func.count()).select_from(model)) == 0
        assert AsyncSession.commit is original_commit
        assert (await send(client, kind, payload(kind))).status_code == 200


@pytest.mark.parametrize("key", ["", "   ", "x" * 129])
@pytest.mark.parametrize("kind", ["tickets", "handoff"])
async def test_invalid_keys_do_not_create_resources(tmp_path, key, kind):
    async with api_context(tmp_path / "api.db") as (client, factory):
        assert (await send(client, kind, payload(kind), key=key)).status_code == 400
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(OperationAudit)) == 0


async def test_admin_audit_records_actor_owner_and_payload_hash(tmp_path):
    async with api_context(tmp_path / "api.db") as (client, factory):
        result = await send(client, "tickets", payload("tickets"), actor=999)
        assert result.status_code == 200
        async with factory() as db:
            audit = await db.scalar(select(OperationAudit))
            assert audit.user_id == 101
            assert audit.request_payload["actor_id"] == 999
            assert audit.request_payload["owner_id"] == 101
            assert audit.request_payload["request"]["content"] == "合成问题"
            assert len(audit.request_payload["request_hash"]) == 64
