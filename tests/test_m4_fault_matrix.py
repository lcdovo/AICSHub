"""隔离SQL预算事实+供应商替身；不向真实模型发请求。"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from app.config.config import settings
from app.models.models import ModelBudgetDay, ModelCallRecord
from app.services.model_gateway import ModelGateway, model_context
from app.services.llm_service import ModelConfig, LLMService
from tests.test_m4_gateway import factory as factory


async def test_parallel_calls_share_one_run_cost_ceiling(factory, monkeypatch):
    monkeypatch.setattr(settings, "LLM_OUTPUT_PRICE_PER_1K_USD", .02)
    monkeypatch.setattr(settings, "LLM_MAX_COST_USD", .001)
    gateway = ModelGateway()
    model = ModelConfig("synthetic", model="synthetic")

    async def attempt():
        try:
            return await gateway.reserve(model, ["input"], "query", 40, "plain")
        except RuntimeError as exc:
            assert str(exc) == "request_model_budget_exceeded"
            return None

    async with model_context(101, "shared-run", factory=factory):
        results = await asyncio.gather(attempt(), attempt())
    assert len([r for r in results if r]) == 1
    async with factory() as db:
        day = await db.scalar(select(ModelBudgetDay))
        assert float(day.cost) <= .001


async def test_deadline_expiring_during_sql_reservation_never_dispatches(factory, monkeypatch):
    gateway = ModelGateway()
    reserve = gateway.reserve

    async def expired(*args, **kwargs):
        ticket = await reserve(*args, **kwargs)
        ticket["deadline"] = time.monotonic() - 1
        return ticket

    monkeypatch.setattr(gateway, "reserve", expired)
    target = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(usage_metadata={})))
    service = SimpleNamespace(_semaphore=asyncio.Semaphore(1), TIMEOUT=1)
    async with model_context(101, "sql-delay", factory=factory):
        with pytest.raises(TimeoutError):
            await gateway.invoke(service, ModelConfig("synthetic", model="synthetic"),
                                 ["input"], target, "query", output_limit=40)
    target.ainvoke.assert_not_awaited()
    async with factory() as db:
        call = await db.scalar(select(ModelCallRecord))
        assert call.status == "not_sent" and call.usage_basis == "not_sent"
        assert (await db.scalar(select(ModelBudgetDay))).tokens == 0


async def test_cancel_waiting_for_local_slot_refunds_only_known_unsent_call(factory, monkeypatch):
    gateway = ModelGateway()
    reserved = asyncio.Event()
    reserve = gateway.reserve

    async def observed(*args, **kwargs):
        ticket = await reserve(*args, **kwargs)
        reserved.set()
        return ticket

    monkeypatch.setattr(gateway, "reserve", observed)
    target = SimpleNamespace(ainvoke=AsyncMock())
    service = SimpleNamespace(_semaphore=asyncio.Semaphore(0), TIMEOUT=10)
    async with model_context(101, "cancel-before-send", factory=factory):
        task = asyncio.create_task(gateway.invoke(
            service, ModelConfig("synthetic", model="synthetic"), ["input"], target,
            "query", output_limit=40))
        await asyncio.wait_for(reserved.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    target.ainvoke.assert_not_awaited()
    async with factory() as db:
        assert (await db.scalar(select(ModelCallRecord))).status == "not_sent"
        day = await db.scalar(select(ModelBudgetDay))
        assert day.tokens == 0 and day.cost == 0


@pytest.mark.parametrize("verified", [False, True])
async def test_settlement_uses_reserved_price_verification_snapshot(factory, monkeypatch, verified):
    monkeypatch.setattr(settings, "LLM_PRICE_VERIFIED", verified)
    gateway = ModelGateway()
    async with model_context(101, "price-snapshot", factory=factory):
        ticket = await gateway.reserve(ModelConfig("synthetic", model="synthetic"),
                                       ["input"], "query", 40, "plain")
    monkeypatch.setattr(settings, "LLM_PRICE_VERIFIED", not verified)
    await gateway.settle(ticket, SimpleNamespace(usage_metadata={"input_tokens": 1, "output_tokens": 1}))
    async with factory() as db:
        call = await db.scalar(select(ModelCallRecord))
        assert call.price_snapshot["verified"] is verified
        assert call.usage_basis == ("provider_usage" if verified else "provider_tokens_est_price")


async def test_stream_expired_before_dispatch_never_starts_provider(factory, monkeypatch):
    from app.services.model_gateway import GATEWAY

    monkeypatch.setattr(settings, "LLM_MAX_TOKENS", 40)
    monkeypatch.setattr("app.services.llm_service.settings.LLM_MAX_TOKENS", 40)
    reserve = GATEWAY.reserve

    async def expired(*args, **kwargs):
        ticket = await reserve(*args, **kwargs)
        ticket["deadline"] = time.monotonic() - 1
        return ticket

    monkeypatch.setattr(GATEWAY, "reserve", expired)
    called = []

    class Stream:
        async def astream(self, *args, **kwargs):
            called.append(True)
            yield SimpleNamespace(content="late")

    service = LLMService()
    async with model_context(101, "stream-expired", factory=factory):
        with pytest.raises(TimeoutError):
            async for _ in service._gateway_stream(
                ModelConfig("synthetic", model="synthetic"), ["input"], Stream(), 101, "stream-expired"
            ):
                pytest.fail("expired stream must not start")
    assert called == []
    async with factory() as db:
        assert (await db.scalar(select(ModelCallRecord))).status == "not_sent"


@pytest.mark.parametrize("failure", [429, 500, "timeout"])
async def test_sdk_fallback_and_understanding_repair_share_six_attempts(factory, monkeypatch, caplog, failure):
    import httpx
    import app.services.llm_service as llm
    from app.agent.request_understanding import understand
    from app.agent.state import AgentState

    monkeypatch.setattr(settings, "LLM_DAILY_USER_TOKEN_BUDGET", 100000)
    monkeypatch.setattr(settings, "LLM_UNDERSTANDING_MAX_TOKENS", 128)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic-private-error-body", request=request)
        return httpx.Response(failure, json={"error": {
            "message": "synthetic-private-error-body", "type": "server_error", "code": "synthetic"
        }})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    original = llm.ChatOpenAI

    def chat_model(**kwargs):
        assert kwargs["max_retries"] == 0
        return original(**kwargs, http_async_client=client)

    monkeypatch.setattr(llm, "ChatOpenAI", chat_model)
    service = LLMService()
    service.RETRY_DELAY = 0
    service.models = [ModelConfig(f"synthetic-{i}", api_key="synthetic-test-key",
                                 api_base="http://offline.invalid/v1", model="synthetic") for i in range(8)]
    try:
        async with model_context(101, "nested-failures", factory=factory):
            value = await understand(service, AgentState(session_id="s", user_id=101,
                                                          user_message="耳机保修多久"))
        assert value is None and len(calls) == 6
        async with factory() as db:
            rows = (await db.scalars(select(ModelCallRecord))).all()
            assert len(rows) == 6 and len({r.id for r in rows}) == 6
            assert all(r.status == "unknown" and r.usage_basis == "unknown_reserved" for r in rows)
            assert all(r.run_id == "nested-failures" and r.purpose == "request_understanding" for r in rows)
            day = await db.scalar(select(ModelBudgetDay))
            assert day.tokens == sum(r.reserved_tokens for r in rows)
        assert "synthetic-private-error-body" not in caplog.text
    finally:
        await service.close()
        await client.aclose()
