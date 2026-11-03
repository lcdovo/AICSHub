"""AC-10-05：演示座席名册与持久指派负载的分配准入。"""
import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models.models import HandoffRequestRecord, HandoffStatus, OperationAudit
from app.services.collaboration import HumanAgent, HumanAgentService
from tests.test_ticket_idempotency import api_context


async def seed(factory, *, load=0, status=HandoffStatus.PENDING):
    async with factory() as db:
        db.add(HandoffRequestRecord(id="target", user_id=101, session_id="session-101",
                                    reason="合成人工请求", priority="normal", status=status))
        for index in range(load):
            db.add(HandoffRequestRecord(id=f"load-{index}", user_id=101, session_id="session-101",
                                        reason="合成已分配请求", priority="normal", status=HandoffStatus.ASSIGNED,
                                        assigned_to="synthetic-agent", assigned_at=datetime.utcnow()))
        await db.commit()


def roster(monkeypatch, *, status="online", memory_load=0, max_load=2):
    service = HumanAgentService()
    service._agents = {"synthetic-agent": HumanAgent("synthetic-agent", "合成座席", status=status,
                                                   current_load=memory_load, max_load=max_load)}
    monkeypatch.setattr("app.routers.tickets.get_collaboration_service", lambda: SimpleNamespace(human_agent_service=service))
    return service


async def snapshot(factory):
    async with factory() as db:
        item = await db.get(HandoffRequestRecord, "target")
        return (item.status, item.assigned_to, item.assigned_at,
                await db.scalar(select(func.count()).select_from(OperationAudit)))


@pytest.mark.parametrize("condition", ["missing", "disabled", "offline", "busy", "memory_full", "database_full"])
async def test_unavailable_or_full_agent_rejected_without_mutation(tmp_path, monkeypatch, condition):
    async with api_context(tmp_path / "assign.db") as (client, factory):
        roster(monkeypatch, status=condition if condition in {"disabled", "offline", "busy"} else "online",
               memory_load=2 if condition == "memory_full" else 0)
        await seed(factory, load=2 if condition == "database_full" else 0)
        before = await snapshot(factory)
        response = await client.post("/api/v1/handoff/target/assign",
                                     params={"agent_id": "missing-agent" if condition == "missing" else "synthetic-agent"},
                                     headers={"X-Test-Actor": "999"})
        assert response.status_code in {404, 409}
        assert await snapshot(factory) == before


async def test_assignment_persists_audit_and_load_then_rejects_full_seat(tmp_path, monkeypatch):
    async with api_context(tmp_path / "assign.db") as (client, factory):
        service = roster(monkeypatch)
        await seed(factory, load=1)
        result = await client.post("/api/v1/handoff/target/assign", params={"agent_id": "synthetic-agent"},
                                   headers={"X-Test-Actor": "999"})
        assert result.status_code == 200
        status, agent, assigned_at, audits = await snapshot(factory)
        assert status == HandoffStatus.ASSIGNED and agent == "synthetic-agent" and assigned_at is not None
        assert audits == 1
        agents = await client.get("/api/v1/agents", headers={"X-Test-Actor": "999"})
        assert agents.json()["data"][0]["current_load"] == 2
        assert service.get_agent("synthetic-agent").current_load == 0  # 不制造与DB不一致的内存增减
        async with factory() as db:
            db.add(HandoffRequestRecord(id="next", user_id=101, session_id="session-101", reason="下一请求", priority="normal"))
            await db.commit()
        rejected = await client.post("/api/v1/handoff/next/assign", params={"agent_id": "synthetic-agent"},
                                     headers={"X-Test-Actor": "999"})
        assert rejected.status_code == 409


async def test_resolved_requests_do_not_consume_capacity(tmp_path, monkeypatch):
    async with api_context(tmp_path / "assign.db") as (client, factory):
        roster(monkeypatch, max_load=1)
        await seed(factory, load=1)
        async with factory() as db:
            (await db.get(HandoffRequestRecord, "load-0")).status = HandoffStatus.RESOLVED
            await db.commit()
        result = await client.post("/api/v1/handoff/target/assign", params={"agent_id": "synthetic-agent"},
                                   headers={"X-Test-Actor": "999"})
        assert result.status_code == 200


@pytest.mark.parametrize("status", [HandoffStatus.ASSIGNED, HandoffStatus.RESOLVED, HandoffStatus.CANCELLED])
async def test_nonpending_request_not_overwritten(tmp_path, monkeypatch, status):
    async with api_context(tmp_path / "assign.db") as (client, factory):
        roster(monkeypatch)
        await seed(factory, status=status)
        before = await snapshot(factory)
        response = await client.post("/api/v1/handoff/target/assign", params={"agent_id": "synthetic-agent"},
                                     headers={"X-Test-Actor": "999"})
        assert response.status_code == 409 and await snapshot(factory) == before


@pytest.mark.parametrize("actor", [101, 202])
async def test_ordinary_user_cannot_assign(tmp_path, monkeypatch, actor):
    async with api_context(tmp_path / "assign.db") as (client, factory):
        roster(monkeypatch)
        await seed(factory)
        before = await snapshot(factory)
        result = await client.post("/api/v1/handoff/target/assign", params={"agent_id": "synthetic-agent"},
                                   headers={"X-Test-Actor": str(actor)})
        assert result.status_code == 403 and await snapshot(factory) == before


async def test_missing_request_does_not_create_assignment(tmp_path, monkeypatch):
    async with api_context(tmp_path / "assign.db") as (client, factory):
        roster(monkeypatch)
        response = await client.post("/api/v1/handoff/missing/assign", params={"agent_id": "synthetic-agent"},
                                     headers={"X-Test-Actor": "999"})
        assert response.status_code == 404
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(OperationAudit)) == 0


async def test_assignment_commit_failure_rolls_back_request_and_audit(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    async with api_context(tmp_path / "assign.db") as (client, factory):
        roster(monkeypatch)
        await seed(factory)
        before = await snapshot(factory)

        async def fail(db):
            await db.flush()
            raise RuntimeError("synthetic assignment commit failure")

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "commit", fail)
            with pytest.raises(RuntimeError, match="synthetic assignment"):
                await client.post("/api/v1/handoff/target/assign", params={"agent_id": "synthetic-agent"},
                                  headers={"X-Test-Actor": "999"})
        assert await snapshot(factory) == before


async def test_same_request_competing_assignments_have_one_winner(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    async with api_context(tmp_path / "assign.db") as (client, factory):
        service = roster(monkeypatch)
        service._agents["second-agent"] = HumanAgent("second-agent", "另一合成座席")
        await seed(factory)
        barrier = asyncio.Barrier(2)
        original = AsyncSession.execute

        async def synchronize(db, statement, *args, **kwargs):
            if getattr(statement, "is_update", False) and statement.table.name == "handoff_requests":
                await asyncio.wait_for(barrier.wait(), timeout=10)
            return await original(db, statement, *args, **kwargs)

        monkeypatch.setattr(AsyncSession, "execute", synchronize)
        results = await asyncio.gather(*[
            client.post("/api/v1/handoff/target/assign", params={"agent_id": agent}, headers={"X-Test-Actor": "999"})
            for agent in ("synthetic-agent", "second-agent")
        ])
        assert sorted(result.status_code for result in results) == [200, 409]
        assert (await snapshot(factory))[3] == 1


async def test_agent_list_filters_keep_persistent_load(tmp_path, monkeypatch):
    async with api_context(tmp_path / "assign.db") as (client, factory):
        service = roster(monkeypatch)
        service.get_agent("synthetic-agent").skills = ["technical"]
        await seed(factory, load=1)
        result = await client.get("/api/v1/agents", params={"skill": "technical", "status": "online"},
                                  headers={"X-Test-Actor": "999"})
        assert result.status_code == 200 and result.json()["data"][0]["current_load"] == 1
        assert (await client.get("/api/v1/agents", params={"status": "offline"},
                                 headers={"X-Test-Actor": "999"})).json()["data"] == []
