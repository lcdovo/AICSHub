import asyncio
from types import SimpleNamespace
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from app.models.models import (
    Base,
    ModelBudgetDay,
    ModelCallRecord,
    ModelConcurrencySlot,
)
from app.services.model_gateway import ModelGateway, model_context
from app.services.llm_service import ModelConfig
from app.config.config import settings


@pytest.fixture
async def factory(tmp_path, monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///" + (tmp_path / "models.db").as_posix(),
        poolclass=NullPool,
        connect_args={"timeout": 20},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENCY", 20)
    monkeypatch.setattr(settings, "LLM_MAX_COST_USD", 1)
    monkeypatch.setattr(settings, "LLM_DAILY_USER_TOKEN_BUDGET", 200)
    yield factory
    await engine.dispose()


async def test_concurrent_atomic_reservations_never_exceed_daily_budget(factory):
    gateway = ModelGateway()
    model = ModelConfig("synthetic", model="synthetic")

    async def claim(i):
        async with model_context(101, "run-" + str(i), factory=factory):
            try:
                return await gateway.reserve(model, ["input"], "intent", 40, "schema")
            except RuntimeError:
                return None

    tickets = [t for t in await asyncio.gather(*(claim(i) for i in range(12))) if t]
    assert 1 <= len(tickets) < 12
    async with factory() as db:
        day = await db.scalar(select(ModelBudgetDay))
        assert day.tokens <= 200
        assert await db.scalar(
            select(func.count()).select_from(ModelCallRecord)
        ) == len(tickets)
    for t in tickets:
        await gateway.settle(
            t, SimpleNamespace(usage_metadata={"input_tokens": 2, "output_tokens": 3})
        )
    async with factory() as db:
        day = await db.scalar(select(ModelBudgetDay))
        assert day.tokens == 5 * len(tickets)
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ModelConcurrencySlot)
                .where(ModelConcurrencySlot.call_id.is_not(None))
            )
            == 0
        )


async def test_unknown_usage_conservatively_retains_reservation_and_settle_is_idempotent(
    factory,
):
    gateway = ModelGateway()
    model = ModelConfig("synthetic", model="synthetic")
    async with model_context(101, "run", factory=factory):
        ticket = await gateway.reserve(model, ["input"], "judge", 40, "schema")
    await gateway.settle(ticket, error=TimeoutError())
    await gateway.settle(
        ticket, SimpleNamespace(usage_metadata={"input_tokens": 1, "output_tokens": 1})
    )
    async with factory() as db:
        record = await db.get(ModelCallRecord, ticket["id"])
        day = await db.scalar(select(ModelBudgetDay))
        assert (
            record.status == "unknown"
            and record.usage_basis == "unknown_reserved"
            and day.tokens == ticket["tokens"]
        )
        assert not hasattr(record, "prompt") and len(record.request_hash) == 64


async def test_two_instances_share_concurrency_slot_and_sql_failure_closes_gate(
    factory, monkeypatch
):
    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENCY", 1)
    model = ModelConfig("synthetic", model="synthetic")
    one, two = ModelGateway(), ModelGateway()
    async with model_context(101, "first", factory=factory):
        first = await one.reserve(model, ["input"], "summary", 40, "schema")
    async with model_context(202, "second", factory=factory):
        with pytest.raises(RuntimeError, match="concurrency"):
            await two.reserve(model, ["input"], "generation", 40, "schema")
    await one.settle(first, SimpleNamespace(usage_metadata={}))
    async with factory() as db:
        assert (
            await db.get(
                ModelBudgetDay,
                "202:" + __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d"),
            )
            is None
        )


async def test_gateway_deadline_prevents_late_result(factory, monkeypatch):
    gateway = ModelGateway()
    model = ModelConfig("synthetic", model="synthetic")

    class Slow:
        async def ainvoke(self, *a):
            await asyncio.sleep(1)
            return "late"

    service = SimpleNamespace(_semaphore=asyncio.Semaphore(1), TIMEOUT=0.02)
    async with model_context(101, "deadline", factory=factory):
        with pytest.raises(TimeoutError):
            await gateway.invoke(
                service, model, ["x"], Slow(), "intent", output_limit=40
            )
    async with factory() as db:
        assert (await db.scalar(select(ModelCallRecord))).status == "unknown"


async def test_all_purposes_have_identity_and_raw_usage(factory, monkeypatch):
    from app.services.llm_service import LLMService, IntentClassification
    from langchain_core.messages import AIMessage

    monkeypatch.setattr(settings, "LLM_DAILY_USER_TOKEN_BUDGET", 100000)
    monkeypatch.setattr(settings, "LLM_API_KEY", "synthetic-test")
    service = LLMService()

    class Structured:
        async def ainvoke(self, messages):
            return {
                "raw": AIMessage(
                    content="",
                    usage_metadata={
                        "input_tokens": 9,
                        "output_tokens": 4,
                        "total_tokens": 13,
                    },
                ),
                "parsed": IntentClassification(intent="technical"),
                "parsing_error": None,
            }

    class Chat:
        root_client = None
        root_async_client = None

        def with_structured_output(self, *a, **kw):
            return Structured()

    monkeypatch.setattr(service, "_get_chat_model", lambda *a, **kw: Chat())
    async with model_context(101, "purpose-run", factory=factory):
        for purpose in ["intent", "query_rewrite", "judge", "summary"]:
            result = await service.run_structured(
                "system", "合成文本", IntentClassification, tags=[purpose]
            )
            assert result.intent == "technical"
    async with factory() as db:
        rows = (await db.execute(select(ModelCallRecord))).scalars().all()
        assert {r.purpose for r in rows} == {
            "intent",
            "query_rewrite",
            "judge",
            "summary",
        }
        assert all(
            r.user_id == 101
            and r.run_id == "purpose-run"
            and r.input_tokens == 9
            and r.output_tokens == 4
            and r.schema_version == "IntentClassification"
            for r in rows
        )


async def test_partial_usage_and_failed_calls_are_not_free(factory):
    gateway = ModelGateway()
    model = ModelConfig("synthetic", model="synthetic")
    async with model_context(101, "partial", factory=factory):
        t = await gateway.reserve(model, ["x"], "intent", 40, "schema")
    await gateway.settle(t, SimpleNamespace(usage_metadata={"input_tokens": 2}))
    async with factory() as db:
        row = await db.get(ModelCallRecord, t["id"])
        assert row.usage_basis == "estimated_reserved" and row.cost > 0


async def test_clients_close_once_even_shared_across_models(monkeypatch):
    from app.services.llm_service import LLMService
    from unittest.mock import AsyncMock, Mock

    service = LLMService()
    async_client = SimpleNamespace(close=AsyncMock())
    sync_client = SimpleNamespace(close=Mock())
    model = SimpleNamespace(root_async_client=async_client, root_client=sync_client)
    service._chat_models = {"one": model, "two": model}
    await service.close()
    async_client.close.assert_awaited_once()
    sync_client.close.assert_called_once()
    assert not service._chat_models


async def test_one_client_close_failure_does_not_skip_other_clients():
    from app.services.llm_service import LLMService
    from unittest.mock import AsyncMock

    service = LLMService()
    broken = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("synthetic-close-failure")))
    remaining = SimpleNamespace(close=AsyncMock())
    service._chat_models = {"one": SimpleNamespace(root_client=None, root_async_client=broken),
                           "two": SimpleNamespace(root_client=None, root_async_client=remaining)}
    with pytest.raises(RuntimeError, match="synthetic-close-failure"):
        await service.close()
    broken.close.assert_awaited_once()
    remaining.close.assert_awaited_once()
    assert not service._chat_models


async def test_closing_one_llm_service_does_not_close_another_services_pool(monkeypatch):
    from app.services.llm_service import LLMService

    one, two = LLMService(), LLMService()
    model = ModelConfig("synthetic", api_base="http://synthetic.invalid/v1",
                        api_key="synthetic-key", model="synthetic")
    first, second = one._get_chat_model(model), two._get_chat_model(model)
    try:
        assert first.root_async_client._client is not second.root_async_client._client
        await one.close()
        assert not second.root_async_client.is_closed()
    finally:
        await one.close()
        await two.close()


async def test_stream_uses_remaining_run_deadline_and_records_unknown(factory, monkeypatch):
    import time
    from app.services.llm_service import LLMService
    from app.services.model_gateway import CALL_CONTEXT

    monkeypatch.setattr(settings, "LLM_MAX_TOKENS", 40)
    monkeypatch.setattr("app.services.llm_service.settings.LLM_MAX_TOKENS", 40)
    service = LLMService()

    class SlowStream:
        async def astream(self, *args, **kwargs):
            await asyncio.sleep(.2)
            yield SimpleNamespace(content="late")

    async with model_context(101, "stream-deadline", factory=factory):
        CALL_CONTEXT.get()["deadline"] = time.monotonic() + .05
        with pytest.raises(TimeoutError):
            async for _ in service._gateway_stream(
                ModelConfig("synthetic", model="synthetic"), ["x"], SlowStream(), 101, "stream-deadline"
            ):
                pytest.fail("late stream must not be published")
    async with factory() as db:
        record = await db.scalar(select(ModelCallRecord))
        assert record.status == "unknown" and record.usage_basis == "unknown_reserved"


async def test_run_shared_cost_ceiling_and_price_snapshot(factory, monkeypatch):
    gateway = ModelGateway()
    model = ModelConfig("synthetic", model="synthetic")
    monkeypatch.setattr(settings, "LLM_MAX_COST_USD", 0.001)
    monkeypatch.setattr(settings, "LLM_OUTPUT_PRICE_PER_1K_USD", 0.02)
    async with model_context(101, "cost-run", factory=factory):
        first = await gateway.reserve(model, ["input"], "understanding", 40, "schema")
        with pytest.raises(RuntimeError, match="request_model_budget_exceeded"):
            await gateway.reserve(model, ["input"], "generation", 40, "schema")
        monkeypatch.setattr(settings, "LLM_OUTPUT_PRICE_PER_1K_USD", 0.9)
        await gateway.settle(
            first,
            SimpleNamespace(usage_metadata={"input_tokens": 1, "output_tokens": 1}),
        )
    async with factory() as db:
        r = await db.get(ModelCallRecord, first["id"])
        assert float(r.cost) < 0.001 and r.price_snapshot["output_per_1k_usd"] == 0.02
