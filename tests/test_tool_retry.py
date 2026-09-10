"""工具执行层单元测试：参数校验、指数退避重试、幂等、熔断。

用内存假工具继承 BaseTool，验证 execute_with_retry 的四个关键行为：
1. validate_params 缺失必填/枚举非法返回 validation 错误
2. retryable 瞬时错误触发指数退避重试直至成功
3. idempotency_key 幂等缓存命中返回上次结果
4. 连续失败超过阈值开启熔断器
"""
import asyncio

import pytest

from app.agent.tools import BaseTool


class FakeTool(BaseTool):
    name = "fake_tool"
    description = "测试假工具"
    parameters = [
        {"name": "order_id", "required": True, "type": "string"},
        {"name": "mode", "required": False, "enum": ["fast", "slow"]},
    ]
    max_retries = 3
    retry_delay_ms = 1  # 测试中缩短退避间隔

    def __init__(self, calls_to_fail: int = 0):
        super().__init__()
        self._calls = 0
        self._calls_to_fail = calls_to_fail

    @property
    def calls(self):
        return self._calls

    async def execute(self, **kwargs):
        self._calls += 1
        if self._calls <= self._calls_to_fail:
            return {
                "success": False,
                "error": "瞬时错误",
                "retryable": True,
            }
        return {"success": True, "data": {"ok": True}}


async def test_validate_params_missing_required():
    tool = FakeTool()
    result = await tool.execute_with_retry(order_id=None)
    assert result.success is False
    assert result.error_type == "validation"
    assert "order_id" in (result.error or "")


async def test_validate_params_invalid_enum():
    tool = FakeTool()
    result = await tool.execute_with_retry(order_id="ORD1", mode="turbo")
    assert result.success is False
    assert result.error_type == "validation"
    assert "mode" in (result.error or "")


async def test_retry_until_success():
    tool = FakeTool(calls_to_fail=1)
    result = await tool.execute_with_retry(order_id="ORD1")
    assert result.success is True
    assert tool.calls == 2  # 首次失败 + 一次重试成功
    assert result.retry_count == 1


async def test_business_error_not_retried():
    """非 retryable 的失败不重试，立即返回业务错误"""

    class BizTool(FakeTool):
        async def execute(self, **kwargs):
            self._calls += 1
            return {"success": False, "error": "订单不存在", "retryable": False}

    tool = BizTool()
    result = await tool.execute_with_retry(order_id="ORD9")
    assert result.success is False
    assert tool.calls == 1
    assert result.error_type == "business"
    assert "订单不存在" in (result.error or "")


async def test_idempotency_cache_hit():
    tool = FakeTool()
    first = await tool.execute_with_retry(
        order_id="ORD1", idempotency_key="req-1"
    )
    second = await tool.execute_with_retry(
        order_id="ORD1", idempotency_key="req-1"
    )
    assert first.success and second.success
    assert tool.calls == 1  # 第二次直接命中缓存


async def test_circuit_breaker_after_consecutive_failures():
    class BoomTool(FakeTool):
        circuit_failure_threshold = 2

        async def execute(self, **kwargs):
            self._calls += 1
            raise RuntimeError("后端不可用")

    tool = BoomTool()
    for _ in range(3):
        await tool.execute_with_retry(order_id="ORD1")
    # 超过阈值后熔断直接拒绝，不再执行
    gate = await tool.execute_with_retry(order_id="ORD1")
    assert gate.success is False
    assert gate.error_type == "circuit_open"
    assert tool.calls <= 4  # 熔断后无新增执行


async def test_timeout_sets_error_type():
    class SlowTool(FakeTool):
        timeout_ms = 5

        async def execute(self, **kwargs):
            await asyncio.sleep(0.2)
            return {"success": True}

    tool = SlowTool()
    result = await tool.execute_with_retry(order_id="ORD1")
    assert result.success is False
    assert result.error_type == "timeout"