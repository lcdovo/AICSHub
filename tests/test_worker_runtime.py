"""Worker schema readiness and heartbeat; no external services or model calls."""
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from scripts import run_worker


def test_heartbeat_requires_live_process_current_schema_and_recent_cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(run_worker, "process_identity", lambda pid: "current-process")
    path = tmp_path / "worker.json"
    assert not run_worker.healthy(path)
    run_worker.heartbeat(path)
    assert run_worker.healthy(path)
    value = json.loads(path.read_text())
    for change in ({"updated_at": time.time() - 200}, {"updated_at": time.time() + 200},
                   {"schema_revision": "0012_agent_tasks"}, {"pid": -1000000}, {"process_started": "old-process"}):
        path.write_text(json.dumps({**value, **change}))
        assert not run_worker.healthy(path)


async def test_schema_gate_rejects_old_revision_and_missing_journal(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///" + (tmp_path / "schema.db").as_posix())
    factory = async_sessionmaker(engine)
    try:
        async with engine.begin() as db:
            await db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(40))"))
            await db.execute(text("INSERT INTO alembic_version VALUES ('0012_agent_tasks')"))
        with pytest.raises(RuntimeError, match="worker_schema_migration_required"):
            await run_worker.verify_schema(factory)
        async with engine.begin() as db:
            await db.execute(text("UPDATE alembic_version SET version_num='0013_agent_reliability'"))
        with pytest.raises(RuntimeError, match="worker_schema_migration_required"):
            await run_worker.verify_schema(factory)
        async with engine.begin() as db:
            await db.execute(text("CREATE TABLE agent_capability_calls (id VARCHAR(36))"))
        await run_worker.verify_schema(factory)
    finally:
        await engine.dispose()


async def test_worker_consumes_summary_and_marks_healthy(tmp_path, monkeypatch):
    from tests.test_ticket_idempotency import api_context
    from app.models.models import BackgroundJob
    async with api_context(tmp_path / "jobs.db") as (_, factory):
        async with factory() as db:
            db.add(BackgroundJob(id="summary-runtime", event_key="summary-runtime", kind="summary",
                                resource_id="session-101", user_id=101))
            await db.commit()
        monkeypatch.setattr("app.utils.database.async_session", factory)
        monkeypatch.setattr(run_worker, "verify_schema", AsyncMock())
        monkeypatch.setattr("app.utils.database.close_redis", AsyncMock())
        path = tmp_path / "worker.json"
        args = SimpleNamespace(once=True, simulate=False, limit=10, kind="summary", poll=.1, heartbeat_file=path)
        await run_worker.run(args)
        assert run_worker.healthy(path)
        async with factory() as db:
            job = await db.get(BackgroundJob, "summary-runtime")
            assert job.status == "succeeded" and job.lease_token is None


async def test_once_failure_does_not_leave_fresh_heartbeat(tmp_path, monkeypatch):
    path = tmp_path / "worker.json"
    run_worker.heartbeat(path)
    monkeypatch.setattr(run_worker, "verify_schema", AsyncMock())
    monkeypatch.setattr(run_worker, "sweep_runs", AsyncMock(side_effect=OSError("synthetic")))
    monkeypatch.setattr("app.utils.database.close_redis", AsyncMock())
    args = SimpleNamespace(once=True, simulate=False, limit=1, kind=None, poll=.1, heartbeat_file=path)
    with pytest.raises(OSError):
        await run_worker.run(args)
    assert not run_worker.healthy(path)
