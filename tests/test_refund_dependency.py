"""AC-09-02 / AC-08-02（退款分支）：查询失败不得构造退款回执。"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.tools import ApplyRefundTool
from app.models.models import Base, Order, User


@pytest.mark.parametrize("order_id", ["ORD20260801", "ORD20260802", "ORD900099"])
@pytest.mark.parametrize("failure_at", ["factory", "enter", "query", "exit"])
async def test_dependency_failure_never_returns_or_caches_receipt(monkeypatch, order_id, failure_at):
    sentinel = "synthetic-secret-connection-string"
    query = AsyncMock(return_value=SimpleNamespace(status_code="shipped", total_amount=17))
    if failure_at == "query":
        query.side_effect = RuntimeError(sentinel)

    @asynccontextmanager
    async def session():
        if failure_at == "enter":
            raise RuntimeError(sentinel)
        yield SimpleNamespace(scalar=query)
        if failure_at == "exit":
            raise RuntimeError(sentinel)

    factory = Mock(side_effect=RuntimeError(sentinel)) if failure_at == "factory" else Mock(side_effect=session)
    monkeypatch.setattr("app.utils.database.async_session", factory)
    generated_id = Mock(return_value="synthetic-generated-refund-id")
    monkeypatch.setattr("app.agent.tools.uuid.uuid4", generated_id)
    tool = ApplyRefundTool()
    arguments = dict(order_id=order_id, reason="合成测试商品破损", user_id=101,
                     idempotency_key="synthetic-refund-key")
    raw = await tool.execute(**arguments)
    assert raw["success"] is False
    assert raw.get("data") is None
    assert raw["error_type"] == "dependency_unavailable"
    assert raw.get("retryable") is False
    assert sentinel not in str(raw)
    # 对调用方可见的包装路径也必须保留失败类型，且不得缓存/自动重试该结果。
    factory.reset_mock()
    result = await tool.execute_with_retry(**arguments)
    assert result.success is False
    assert result.error_type == "dependency_unavailable"
    assert result.data is None and result.retry_count == 0
    assert factory.call_count == 1
    assert tool._idempotency_cache == {}
    generated_id.assert_not_called()


async def test_missing_and_foreign_orders_remain_rejected(tmp_path, monkeypatch):
    """使用真实临时 SQLite 验证归属查询，不用替身假装执行 SQL。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'refund.db').as_posix()}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(User(id=202, username="synthetic-b", role="user"))
            await session.flush()
            session.add(Order(order_id="ORD20260801", user_id=202, status="已发货",
                              status_code="shipped", total_amount=17, items=[]))
            await session.commit()
        monkeypatch.setattr("app.utils.database.async_session", factory)
        for order_id in ("ORD20260801", "ORD900099"):
            result = await ApplyRefundTool().execute(order_id=order_id, reason="合成测试商品破损", user_id=101)
            assert result["success"] is False
            assert result.get("data") is None
            assert "17" not in str(result)
        async with factory() as session:
            assert (await session.scalar(select(Order))).user_id == 202
    finally:
        await engine.dispose()
