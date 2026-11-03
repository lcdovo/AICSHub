"""真实隔离MySQL，多实际进程模型预算；无外部模型调用。"""

import asyncio, multiprocessing
from datetime import datetime, timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool


@pytest.fixture
def real_case():
    import uuid
    from sqlalchemy import create_engine, text
    from app.models.models import Base
    from app.repositories.knowledge_repository import KnowledgeRepository

    admin = create_engine(settings.DATABASE_URL.replace("+aiomysql", "+pymysql"))
    name = "aicshub_m4_case_" + uuid.uuid4().hex[:12]
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as db:
        db.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4"))
    url = admin.url.set(database=name)
    repo = KnowledgeRepository(url)
    Base.metadata.create_all(repo.engine)
    admin.dispose()
    yield SimpleNamespace(_repository=repo)
    repo.engine.dispose()


from app.config.config import settings
from app.models.models import ModelBudgetDay, ModelCallRecord, ModelConcurrencySlot


def budget_worker(url, barrier, queue, worker_index):
    async def run():
        from app.services.model_gateway import ModelGateway, model_context
        from app.services.llm_service import ModelConfig

        engine = create_async_engine(
            url.replace("+pymysql", "+aiomysql"), poolclass=NullPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        settings.LLM_DAILY_USER_TOKEN_BUDGET = 60
        settings.LLM_DAILY_USER_COST_USD = 1
        settings.LLM_MAX_COST_USD = 1
        settings.LLM_MAX_CONCURRENCY = 1
        for i in range(20):
            await asyncio.to_thread(barrier.wait, 30)
            async with model_context(
                (910000 + i if i % 2 == 0 else 920000 + i + worker_index * 100),
                "synthetic-run-" + str(i),
                factory=factory,
            ):
                try:
                    ticket = await ModelGateway().reserve(
                        ModelConfig("synthetic", model="fake"),
                        ["input"],
                        "intent",
                        40,
                        "schema",
                    )
                    queue.put(ticket["id"])
                except RuntimeError:
                    queue.put(None)
            await asyncio.to_thread(barrier.wait, 30)
        await engine.dispose()

    asyncio.run(run())


def test_two_actual_workers_share_daily_budget_and_global_slot_20_rounds(real_case):
    repo = real_case._repository
    url = repo.engine.url.render_as_string(hide_password=False)
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(3)
    queue = ctx.Queue()
    workers = [
        ctx.Process(target=budget_worker, args=(url, barrier, queue, worker_index))
        for worker_index in range(2)
    ]
    for w in workers:
        w.start()
    for i in range(20):
        barrier.wait(30)
        ids = [queue.get(timeout=30) for _ in workers]
        if sum(v is not None for v in ids) != 1:
            barrier.abort()
            for w in workers:
                w.join(2)
                if w.is_alive():
                    w.terminate()
                    w.join(2)
            pytest.fail("expected exactly one valid SQL budget/slot owner")
        with repo.transaction() as db:
            days = db.scalars(select(ModelBudgetDay)).all()
            assert all(day.tokens <= 60 for day in days)
            slot = db.get(ModelConcurrencySlot, 1)
            slot.call_id = None
            slot.expires_at = None
        barrier.wait(30)
    for w in workers:
        w.join(10)
        assert w.exitcode == 0
    with repo.transaction() as db:
        assert db.scalar(select(func.count()).select_from(ModelCallRecord)) == 20


def test_gateway_process_restart_unknown_reservation_not_released_as_free(real_case):
    from app.services.model_gateway import ModelGateway, model_context
    from app.services.llm_service import ModelConfig

    url = real_case._repository.engine.url.set(drivername="mysql+aiomysql")

    async def run():
        engine = create_async_engine(url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        settings.LLM_DAILY_USER_TOKEN_BUDGET = 200
        settings.LLM_MAX_CONCURRENCY = 1
        settings.LLM_DAILY_USER_COST_USD = 1
        settings.LLM_MAX_COST_USD = 1
        async with model_context(101, "old-worker", factory=factory):
            old = await ModelGateway().reserve(
                ModelConfig("fake", model="fake"), ["input"], "intent", 40, "schema"
            )
        async with factory() as db:
            (await db.get(ModelCallRecord, old["id"])).expires_at = (
                datetime.utcnow() - timedelta(seconds=1)
            )
            (await db.get(ModelConcurrencySlot, 1)).expires_at = (
                datetime.utcnow() - timedelta(seconds=1)
            )
            await db.commit()
        async with model_context(101, "new-worker", factory=factory):
            new = await ModelGateway().reserve(
                ModelConfig("fake", model="fake"), ["input"], "intent", 40, "schema"
            )
        async with factory() as db:
            assert (await db.get(ModelCallRecord, old["id"])).status == "unknown"
            assert (await db.scalar(select(ModelBudgetDay))).tokens == old[
                "tokens"
            ] + new["tokens"]
        await ModelGateway().settle(
            new, SimpleNamespace(usage_metadata={"input_tokens": 2, "output_tokens": 3})
        )
        await engine.dispose()

    asyncio.run(run())
