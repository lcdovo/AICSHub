"""AC-10-06：不同请求同时争抢最后名额，容量必须在DB事务中串行判定。"""
import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.models import HandoffRequestRecord, HandoffStatus, OperationAudit, User
from app.services.collaboration import HumanAgent, HumanAgentService
from app.services.handoff_assignment import assign_request
from tests.test_ticket_idempotency import api_context


def agents():
    service = HumanAgentService()
    service._agents = {"synthetic-agent": HumanAgent("synthetic-agent", "合成座席", max_load=1)}
    return service


async def requests(factory, prefix="r", count=10):
    async with factory() as db:
        db.add_all([HandoffRequestRecord(id=f"{prefix}-{index}", user_id=101, session_id="session-101",
                                        reason="合成容量请求", priority="normal") for index in range(count)])
        await db.commit()


@pytest.mark.parametrize("repetition", range(20))
async def test_last_slot_has_one_winner_across_independent_sessions(tmp_path, monkeypatch, repetition):
    from fastapi import HTTPException
    path = tmp_path / "capacity.db"
    async with api_context(path) as (_, factory):
        await requests(factory)
        # 第二引擎/会话工厂模拟独立worker连接，没有共享进程锁或座席对象。
        second_engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}", connect_args={"timeout": 30})
        second_factory = async_sessionmaker(second_engine, expire_on_commit=False)
        barrier = asyncio.Barrier(10)
        original_get = AsyncSession.get

        async def synchronize(db, entity, ident, **kwargs):
            value = await original_get(db, entity, ident, **kwargs)
            if entity is HandoffRequestRecord and str(ident).startswith("r-"):
                await asyncio.wait_for(barrier.wait(), timeout=20)
            return value

        monkeypatch.setattr(AsyncSession, "get", synchronize)

        async def attempt(index):
            current_factory = factory if index % 2 else second_factory
            async with current_factory() as db:
                try:
                    await assign_request(db, await db.get(User, 999), f"r-{index}", "synthetic-agent", agents())
                    return 200
                except HTTPException as exc:
                    await db.rollback()
                    return exc.status_code

        try:
            results = await asyncio.gather(*[attempt(i) for i in range(10)])
            assert results.count(200) == 1
            assert results.count(409) == 9
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(HandoffRequestRecord).where(
                    HandoffRequestRecord.status == HandoffStatus.ASSIGNED)) == 1
                assert await db.scalar(select(func.count()).select_from(OperationAudit)) == 1
        finally:
            await second_engine.dispose()


async def test_resolved_assignment_releases_capacity_without_counter_repair(tmp_path):
    async with api_context(tmp_path / "release.db") as (_, factory):
        await requests(factory, count=2)
        async with factory() as db:
            await assign_request(db, await db.get(User, 999), "r-0", "synthetic-agent", agents())
        async with factory() as db:
            (await db.get(HandoffRequestRecord, "r-0")).status = HandoffStatus.RESOLVED
            await db.commit()
        # 新引擎+新名册仍只依据持久有效指派，无进程内计数需要释放。
        async with factory() as db:
            await assign_request(db, await db.get(User, 999), "r-1", "synthetic-agent", agents())
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(HandoffRequestRecord).where(
                HandoffRequestRecord.status == HandoffStatus.ASSIGNED)) == 1
            assert await db.scalar(select(func.count()).select_from(OperationAudit)) == 2


async def test_failed_assignment_does_not_reserve_last_slot(tmp_path, monkeypatch):
    async with api_context(tmp_path / "rollback.db") as (_, factory):
        await requests(factory, count=2)

        async def fail_commit(db):
            await db.flush()
            raise RuntimeError("synthetic assignment failure")

        async with factory() as db:
            with monkeypatch.context() as patch:
                patch.setattr(AsyncSession, "commit", fail_commit)
                with pytest.raises(RuntimeError, match="synthetic"):
                    await assign_request(db, await db.get(User, 999), "r-0", "synthetic-agent", agents())
        async with factory() as db:
            await assign_request(db, await db.get(User, 999), "r-1", "synthetic-agent", agents())
        async with factory() as db:
            assert (await db.get(HandoffRequestRecord, "r-0")).status == HandoffStatus.PENDING
            assert await db.scalar(select(func.count()).select_from(OperationAudit)) == 1


def test_additive_migration_preserves_existing_assignments(tmp_path):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect
    from app.models.models import Base, HandoffCapacityGate

    path = Path(__file__).resolve().parents[1] / "alembic/versions/0005_handoff_capacity.py"
    spec = importlib.util.spec_from_file_location("capacity_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    try:
        with engine.begin() as conn:
            # 模拟升级前表结构（仅缺新增闸门表），含历史指派及未知老座席ID。
            Base.metadata.create_all(conn, tables=[t for t in Base.metadata.sorted_tables if t.name != HandoffCapacityGate.__tablename__])
            conn.execute(HandoffRequestRecord.__table__.insert().values(
                id="historical", user_id=101, session_id="old-session", reason="历史合成请求",
                priority="normal", status=HandoffStatus.ASSIGNED, assigned_to="legacy-agent"))
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                migration.upgrade()  # 空库metadata可能已建表，保持upgrade兼容。
                assert inspect(conn).has_table("handoff_capacity_gates")
                before = conn.execute(select(HandoffRequestRecord.__table__)).mappings().all()
                migration.downgrade()
                assert not inspect(conn).has_table("handoff_capacity_gates")
                assert conn.execute(select(HandoffRequestRecord.__table__)).mappings().all() == before
                migration.upgrade()
                assert conn.execute(select(HandoffRequestRecord.assigned_to)).scalar() == "legacy-agent"
    finally:
        engine.dispose()
