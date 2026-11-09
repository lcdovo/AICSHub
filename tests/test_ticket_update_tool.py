"""AC-10-02/03：工单更新必须受权、持久且遵循既有状态机。"""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import func, select

from app.agent.tools import UpdateTicketStatusTool
from app.config.config import settings
from app.models.models import OperationAudit, Ticket, TicketPriority, TicketStatus, User
from tests.test_ticket_idempotency import api_context


def args(ticket_id="synthetic-ticket", status="processing"):
    return {"ticket_id": ticket_id, "status": status, "note": "合成处理备注", "idempotency_key": "update-k"}


def capability(payload, actor=999):
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return jwt.encode({"purpose": "update_ticket_status", "sub": str(actor), "request_hash": digest,
                       "exp": datetime.now(timezone.utc) + timedelta(minutes=2)}, settings.AUTH_SECRET_KEY, algorithm="HS256")


async def seed(factory, status=TicketStatus.PENDING):
    async with factory() as db:
        db.add(Ticket(id="synthetic-ticket", user_id=101, category="技术支持", content="合成问题",
                      priority=TicketPriority.MEDIUM, status=status))
        await db.commit()


async def snapshot(factory):
    async with factory() as db:
        item = await db.get(Ticket, "synthetic-ticket")
        return (item.status, item.updated_at, item.resolution,
                await db.scalar(select(func.count()).select_from(OperationAudit)))


async def test_missing_ticket_never_reports_updated(tmp_path, monkeypatch):
    async with api_context(tmp_path / "update.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        payload = args(ticket_id="missing-ticket")
        result = await UpdateTicketStatusTool().execute(**payload, authorization=capability(payload))
        assert not result["success"] and result["error_type"] == "not_found"
        assert not result.get("data")


@pytest.mark.parametrize("actor", [101, 202])
async def test_ordinary_users_cannot_update_own_or_foreign_ticket(tmp_path, monkeypatch, actor):
    async with api_context(tmp_path / "update.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        before = await snapshot(factory)
        payload = args()
        result = await UpdateTicketStatusTool().execute(**payload, authorization=capability(payload, actor))
        assert not result["success"] and result["error_type"] == "permission"
        assert await snapshot(factory) == before


@pytest.mark.parametrize("initial,target", [(TicketStatus.PENDING, "closed"), (TicketStatus.CLOSED, "processing"),
                                           (TicketStatus.RESOLVED, "pending")])
async def test_illegal_transition_leaves_database_unchanged(tmp_path, monkeypatch, initial, target):
    async with api_context(tmp_path / "update.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory, initial)
        before = await snapshot(factory)
        payload = args(status=target)
        result = await UpdateTicketStatusTool().execute(**payload, authorization=capability(payload))
        assert not result["success"] and result["error_type"] == "conflict"
        assert await snapshot(factory) == before


@pytest.mark.parametrize("initial,target", [(TicketStatus.PENDING, "processing"),
                                           (TicketStatus.PROCESSING, "resolved"),
                                           (TicketStatus.RESOLVED, "closed")])
async def test_authorized_update_is_visible_and_replays_after_new_instance(tmp_path, monkeypatch, initial, target):
    async with api_context(tmp_path / "update.db") as (client, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory, initial)
        payload = args(status=target)
        result = await UpdateTicketStatusTool().execute(**payload, authorization=capability(payload))
        assert result["success"]
        assert result["data"]["old_status"] == initial.value
        assert result["data"]["new_status"] == target
        assert (await client.get("/api/v1/tickets/synthetic-ticket")).json()["data"]["status"] == target
        assert (await snapshot(factory))[3] == 1
        second = await UpdateTicketStatusTool().execute(**payload, authorization=capability(payload))
        assert second["data"] == result["data"]
        assert (await snapshot(factory))[3] == 1


@pytest.mark.parametrize("change", ["missing_auth", "status", "note", "extra"])
async def test_untrusted_or_changed_payload_does_not_update(tmp_path, monkeypatch, change):
    async with api_context(tmp_path / "update.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        before = await snapshot(factory)
        payload = args()
        authorization = capability(payload)
        if change == "missing_auth":
            authorization = ""
        elif change == "status":
            payload["status"] = "escalated"
        elif change == "note":
            payload["note"] = "修改备注"
        else:
            payload["actor_id"] = 999
        result = await UpdateTicketStatusTool().execute(**payload, authorization=authorization)
        assert not result["success"] and await snapshot(factory) == before


async def test_database_failure_is_not_a_success(monkeypatch):
    def unavailable():
        raise RuntimeError("synthetic-private-db")
    monkeypatch.setattr("app.utils.database.async_session", unavailable)
    payload = args()
    result = await UpdateTicketStatusTool().execute_with_retry(**payload, authorization=capability(payload))
    assert not result.success and result.data is None
    assert "synthetic-private-db" not in str(result)


@pytest.mark.parametrize("field,value", [("priority", "urgent"), ("assigned_to", "synthetic-agent"),
                                        ("resolution", "合成处理结果")])
async def test_api_other_fields_and_admin_audit_are_preserved(tmp_path, field, value):
    async with api_context(tmp_path / "update.db") as (client, factory):
        await seed(factory)
        response = await client.put("/api/v1/tickets/synthetic-ticket", json={field: value},
                                    headers={"X-Test-Actor": "999"})
        assert response.status_code == 200
        assert response.json()["data"][field] == value
        async with factory() as db:
            audit = await db.scalar(select(OperationAudit))
            assert audit.user_id == 101 and audit.request_payload["actor_id"] == 999
            assert audit.request_payload["changes"] == {field: value}


async def test_api_mixed_invalid_update_does_not_partially_apply(tmp_path):
    async with api_context(tmp_path / "update.db") as (client, factory):
        await seed(factory)
        before = await snapshot(factory)
        response = await client.put("/api/v1/tickets/synthetic-ticket",
                                    json={"status": "processing", "priority": "invalid"},
                                    headers={"X-Test-Actor": "999"})
        assert response.status_code == 400 and await snapshot(factory) == before


@pytest.mark.parametrize("actor,code", [(101, 403), (202, 403), (999, 404)])
async def test_api_permissions_and_missing_resource_match_tool(tmp_path, actor, code):
    async with api_context(tmp_path / "update.db") as (client, _):
        response = await client.put("/api/v1/tickets/missing-ticket", json={"status": "processing"},
                                    headers={"X-Test-Actor": str(actor)})
        assert response.status_code == code


async def test_api_and_tool_share_key_and_original_operation_receipt(tmp_path, monkeypatch):
    async with api_context(tmp_path / "update.db") as (client, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        payload = {**args(), "note": ""}
        first = await UpdateTicketStatusTool().execute(**payload, authorization=capability(payload))
        replay = await client.put("/api/v1/tickets/synthetic-ticket", json={"status": "processing"},
                                  headers={"X-Test-Actor": "999", "Idempotency-Key": "update-k"})
        assert replay.status_code == 200
        assert (await snapshot(factory))[3] == 1
        # 之后合法推进到resolved，旧键重放仍是原操作回执，不倒退当前数据库状态。
        assert (await client.put("/api/v1/tickets/synthetic-ticket", json={"status": "resolved"},
                                 headers={"X-Test-Actor": "999"})).status_code == 200
        second = await UpdateTicketStatusTool().execute(**payload, authorization=capability(payload))
        assert second["data"] == first["data"]
        assert (await snapshot(factory))[0] == TicketStatus.RESOLVED
        assert (await snapshot(factory))[3] == 2


async def test_changed_signed_payload_conflicts_with_original_key(tmp_path, monkeypatch):
    async with api_context(tmp_path / "update.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        tool = UpdateTicketStatusTool()
        payload = args()
        assert (await tool.execute_with_retry(**payload, authorization=capability(payload))).success
        before = await snapshot(factory)
        payload["status"] = "escalated"
        changed = await tool.execute_with_retry(**payload, authorization=capability(payload))
        assert not changed.success and changed.error_type == "conflict"
        assert await snapshot(factory) == before


@pytest.mark.parametrize("change", ["disabled", "role", "expired", "purpose"])
async def test_current_admin_and_bound_purpose_required(tmp_path, monkeypatch, change):
    async with api_context(tmp_path / "update.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        payload = args()
        token = capability(payload)
        if change in {"disabled", "role"}:
            async with factory() as db:
                admin = await db.get(User, 999)
                if change == "disabled":
                    admin.status = False
                else:
                    admin.role = "user"
                await db.commit()
        else:
            claims = jwt.decode(token, settings.AUTH_SECRET_KEY, algorithms=["HS256"])
            if change == "expired":
                claims["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
            else:
                claims["purpose"] = "create_ticket"
            token = jwt.encode(claims, settings.AUTH_SECRET_KEY, algorithm="HS256")
        result = await UpdateTicketStatusTool().execute(**payload, authorization=token)
        assert not result["success"]
        assert (await snapshot(factory))[0] == TicketStatus.PENDING
        assert (await snapshot(factory))[3] == 0


@pytest.mark.parametrize("stage", ["before", "after"])
async def test_commit_fault_rolls_back_or_replays_without_new_audit(tmp_path, monkeypatch, stage):
    from sqlalchemy.ext.asyncio import AsyncSession
    async with api_context(tmp_path / "update.db") as (_, factory):
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
            result = await UpdateTicketStatusTool().execute_with_retry(**payload, authorization=capability(payload))
            assert not result.success and result.data is None and result.retry_count == 0
            assert "synthetic-private-commit" not in str(result)
        assert (await snapshot(factory))[3] == (1 if stage == "after" else 0)
        replay = await UpdateTicketStatusTool().execute(**payload, authorization=capability(payload))
        assert replay["success"] and (await snapshot(factory))[3] == 1


async def test_concurrent_different_updates_use_cas(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    async with api_context(tmp_path / "update.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        barrier = asyncio.Barrier(2)
        original = AsyncSession.flush

        async def synchronize(db, *args, **kwargs):
            if any(isinstance(item, OperationAudit) and item.operation == "update_ticket_status" for item in db.new):
                await asyncio.wait_for(barrier.wait(), timeout=10)
            return await original(db, *args, **kwargs)

        monkeypatch.setattr(AsyncSession, "flush", synchronize)
        payloads = [{**args(status=status), "idempotency_key": f"race-{status}"}
                    for status in ("processing", "escalated")]
        results = await asyncio.gather(*[UpdateTicketStatusTool().execute(**p, authorization=capability(p)) for p in payloads])
        assert sum(item["success"] for item in results) == 1
        assert next(item for item in results if not item["success"])["error_type"] == "conflict"
        assert (await snapshot(factory))[3] == 1


async def test_same_key_ten_requests_only_update_once(tmp_path, monkeypatch):
    async with api_context(tmp_path / "update.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        payload = args()
        results = await asyncio.gather(*[UpdateTicketStatusTool().execute(**payload, authorization=capability(payload)) for _ in range(10)])
        assert all(item["success"] for item in results)
        assert all(item["data"] == results[0]["data"] for item in results)
        assert (await snapshot(factory))[3] == 1


async def test_inprocess_mcp_has_same_update_receipt(tmp_path, monkeypatch):
    from app.agent.mcp_client import _extract_text_content, _inprocess_session
    from app.agent.ticket_authorization import authorize_ticket_update
    async with api_context(tmp_path / "update.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed(factory)
        payload = args()
        async with _inprocess_session() as client:
            await client.initialize()
            raw = await client.call_tool("update_ticket_status", {**payload, "authorization": authorize_ticket_update(payload, 999)})
            result = json.loads(_extract_text_content(raw))
            assert result["success"] and result["data"]["new_status"] == "processing"
        assert (await snapshot(factory))[0] == TicketStatus.PROCESSING
