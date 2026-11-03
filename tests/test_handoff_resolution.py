"""AC-10-07解决分支：结果不可被重放覆盖，agent_id是归属断言。"""
import asyncio
from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.models.models import HandoffRequestRecord, HandoffStatus, OperationAudit, User
from tests.test_ticket_idempotency import api_context


async def seed(factory, status=HandoffStatus.ASSIGNED, agent="synthetic-agent"):
    async with factory() as db:
        db.add(HandoffRequestRecord(id="target", user_id=101, session_id="session-101", reason="合成请求",
                                    priority="normal", status=status, assigned_to=agent,
                                    assigned_at=datetime.utcnow() if agent else None))
        await db.commit()


async def snapshot(factory):
    async with factory() as db:
        item = await db.get(HandoffRequestRecord, "target")
        return (item.status, item.assigned_to, item.resolution, item.resolved_at,
                await db.scalar(select(func.count()).select_from(OperationAudit)))


async def resolve(client, resolution="合成处理结果", agent="synthetic-agent", actor=999):
    params = {"resolution": resolution}
    if agent is not None:
        params["agent_id"] = agent
    return await client.post("/api/v1/handoff/target/resolve", params=params, headers={"X-Test-Actor": str(actor)})


async def test_resolution_replay_preserves_result_timestamp_and_single_audit(tmp_path):
    async with api_context(tmp_path / "resolve.db") as (client, factory):
        await seed(factory)
        first = await resolve(client)
        assert first.status_code == 200
        before = await snapshot(factory)
        assert before[0] == HandoffStatus.RESOLVED and before[4] == 1
        second = await resolve(client)
        assert second.status_code == 200 and second.json()["data"] == first.json()["data"]
        assert await snapshot(factory) == before
        assert (await client.get("/api/v1/handoff/requests/target")).json()["data"]["resolution"] == "合成处理结果"


async def test_changed_resolution_conflicts_without_overwriting(tmp_path):
    async with api_context(tmp_path / "resolve.db") as (client, factory):
        await seed(factory)
        assert (await resolve(client)).status_code == 200
        before = await snapshot(factory)
        assert (await resolve(client, resolution="另一个处理结果")).status_code == 409
        assert await snapshot(factory) == before


@pytest.mark.parametrize("status,agent", [(HandoffStatus.ASSIGNED, "synthetic-agent"), (HandoffStatus.PENDING, None)])
async def test_agent_parameter_cannot_reassign_request(tmp_path, status, agent):
    async with api_context(tmp_path / "resolve.db") as (client, factory):
        await seed(factory, status, agent)
        before = await snapshot(factory)
        assert (await resolve(client, agent="different-agent")).status_code == 409
        assert await snapshot(factory) == before


@pytest.mark.parametrize("resolution", ["", "   ", "x" * 10001], ids=["empty", "whitespace", "too_long"])
async def test_invalid_resolution_has_no_side_effects(tmp_path, resolution):
    async with api_context(tmp_path / "resolve.db") as (client, factory):
        await seed(factory)
        before = await snapshot(factory)
        response = await resolve(client, resolution=resolution)
        assert response.status_code in {400, 422} and await snapshot(factory) == before


@pytest.mark.parametrize("actor", [101, 202])
async def test_nonadmin_cannot_resolve(tmp_path, actor):
    async with api_context(tmp_path / "resolve.db") as (client, factory):
        await seed(factory)
        before = await snapshot(factory)
        assert (await resolve(client, actor=actor)).status_code == 403
        assert await snapshot(factory) == before


@pytest.mark.parametrize("different", [False, True])
async def test_concurrent_resolutions_have_single_transition_and_audit(tmp_path, monkeypatch, different):
    from sqlalchemy.ext.asyncio import AsyncSession
    async with api_context(tmp_path / "resolve.db") as (client, factory):
        await seed(factory)
        barrier = asyncio.Barrier(2)
        original = AsyncSession.get

        async def synchronize(db, entity, ident, **kwargs):
            value = await original(db, entity, ident, **kwargs)
            if entity is HandoffRequestRecord and ident == "target" and value.status == HandoffStatus.ASSIGNED:
                await asyncio.wait_for(barrier.wait(), timeout=10)
            return value

        monkeypatch.setattr(AsyncSession, "get", synchronize)
        responses = await asyncio.gather(resolve(client), resolve(client, resolution="不同结果" if different else "合成处理结果"))
        monkeypatch.setattr(AsyncSession, "get", original)
        assert sorted(item.status_code for item in responses) == ([200, 409] if different else [200, 200])
        assert (await snapshot(factory))[4] == 1


@pytest.mark.parametrize("stage", ["before", "after"])
async def test_commit_fault_and_replay_keep_single_result(tmp_path, monkeypatch, stage):
    from sqlalchemy.ext.asyncio import AsyncSession
    async with api_context(tmp_path / "resolve.db") as (client, factory):
        await seed(factory)
        original = AsyncSession.commit

        async def fail(db):
            if stage == "after":
                await original(db)
            else:
                await db.flush()
            raise RuntimeError("synthetic resolution commit failure")

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "commit", fail)
            with pytest.raises(RuntimeError, match="synthetic"):
                await resolve(client)
        state = await snapshot(factory)
        assert state[0] == (HandoffStatus.RESOLVED if stage == "after" else HandoffStatus.ASSIGNED)
        assert state[4] == (1 if stage == "after" else 0)
        assert (await resolve(client)).status_code == 200
        assert (await snapshot(factory))[4] == 1
        if stage == "after":
            assert await snapshot(factory) == state


async def test_legacy_resolved_request_replays_without_fabricating_historical_audit(tmp_path):
    async with api_context(tmp_path / "resolve.db") as (client, factory):
        await seed(factory, HandoffStatus.RESOLVED)
        async with factory() as db:
            item = await db.get(HandoffRequestRecord, "target")
            item.resolution = "旧记录处理结果"
            item.resolved_at = datetime(2026, 1, 1)
            await db.commit()
        before = await snapshot(factory)
        assert (await resolve(client, resolution="旧记录处理结果", agent=None)).status_code == 200
        assert await snapshot(factory) == before


@pytest.mark.parametrize("status", [HandoffStatus.PENDING, HandoffStatus.CANCELLED])
async def test_existing_unresolved_business_scope_is_preserved(tmp_path, status):
    async with api_context(tmp_path / "resolve.db") as (client, factory):
        await seed(factory, status=status, agent=None)
        response = await resolve(client, agent=None)
        assert response.status_code == 200
        assert (await snapshot(factory))[0] == HandoffStatus.RESOLVED
        assert (await snapshot(factory))[1] is None


async def test_missing_request_returns_404(tmp_path):
    async with api_context(tmp_path / "resolve.db") as (client, _):
        assert (await resolve(client)).status_code == 404


async def test_resolution_releases_capacity_and_keeps_actor_owner_audit(tmp_path, monkeypatch):
    from tests.test_handoff_assignment import roster
    async with api_context(tmp_path / "resolve.db") as (client, factory):
        roster(monkeypatch, max_load=1)
        await seed(factory)
        assert (await resolve(client)).status_code == 200
        async with factory() as db:
            audit = await db.scalar(select(OperationAudit))
            assert audit.operation == "resolve_handoff" and audit.user_id == 101
            assert audit.request_payload["actor_id"] == 999 and audit.request_payload["agent_id"] == "synthetic-agent"
            db.add(HandoffRequestRecord(id="next", user_id=101, session_id="session-101", reason="下一请求", priority="normal"))
            await db.commit()
        result = await client.post("/api/v1/handoff/next/assign", params={"agent_id": "synthetic-agent"},
                                   headers={"X-Test-Actor": "999"})
        assert result.status_code == 200
        assert (await client.get("/api/v1/agents", headers={"X-Test-Actor": "999"})).json()["data"][0]["current_load"] == 1


@pytest.mark.parametrize("change", ["disabled", "demoted"])
async def test_service_rechecks_current_admin_even_for_replay(tmp_path, change):
    from fastapi import HTTPException
    from app.services.handoff_resolution import resolve_request
    async with api_context(tmp_path / "resolve.db") as (client, factory):
        await seed(factory)
        assert (await resolve(client)).status_code == 200
        before = await snapshot(factory)
        async with factory() as db:
            stale_actor = await db.get(User, 999)
            # 独立事务变更身份，服务不能因为旧会话缓存为admin而允许重放。
            async with factory() as other:
                admin = await other.get(User, 999)
                if change == "disabled":
                    admin.status = False
                else:
                    admin.role = "user"
                await other.commit()
            await db.commit()  # 结束旧读事务，与API准入后再服务检查的边界一致。
            with pytest.raises(HTTPException) as error:
                await resolve_request(db, stale_actor, "target", "合成处理结果", "synthetic-agent")
            assert error.value.status_code == 403
        assert await snapshot(factory) == before
