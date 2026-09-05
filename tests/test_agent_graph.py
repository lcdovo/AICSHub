"""第 4 步：统一 LangGraph 主链路与工具契约测试。"""
import asyncio

from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.agent.tools import BaseTool, get_tool


def test_run_and_stream_share_the_same_node_order():
    async def scenario():
        graph = AgentGraph()
        normal_state = AgentState(session_id="normal", user_id=1, user_message="请查询订单 ORD20260801")
        stream_state = AgentState(session_id="stream", user_id=1, user_message="请查询订单 ORD20260801")

        normal = await graph.run(normal_state)
        events = [event async for event in graph.run_stream(stream_state)]
        streamed_nodes = [event["node"] for event in events if event["type"] == "node_complete"]
        normal_nodes = [entry["node"] for entry in normal.trace]

        assert streamed_nodes == normal_nodes
        assert normal.reply == stream_state.reply
        assert [call.tool_name for call in normal.tool_calls] == [
            call["tool"] for call in events if call["type"] == "tool_call_complete"
        ]
        assert events[0]["type"] == "node_start"
        assert events[1]["type"] == "stream_mode"
        assert events[1]["token_stream"] == "simulated"

    asyncio.run(scenario())


def test_refund_requires_explicit_confirmation_before_side_effect():
    async def scenario():
        graph = AgentGraph()
        preview_state = AgentState(
            session_id="refund-preview",
            user_id=1,
            user_message="我要退款，订单号 ORD20260801，商品不合适",
        )
        preview = await graph.run(preview_state)

        assert preview.pending_confirmations == ["apply_refund"]
        assert not any(call.tool_name == "apply_refund" for call in preview.tool_calls)
        assert "确认" in preview.reply

        confirmed_state = AgentState(
            session_id="refund-confirmed",
            user_id=1,
            user_message="确认",
            messages=preview.messages,
            collected_info=preview.collected_info,
        )
        confirmed = await graph.run(confirmed_state)
        assert any(call.tool_name == "apply_refund" and call.success for call in confirmed.tool_calls)
        assert confirmed.pending_confirmations == []

    asyncio.run(scenario())


def test_exchange_refund_type_is_supported_by_tool_contract():
    async def scenario():
        result = await get_tool("apply_refund").execute_with_retry(
            order_id="ORD20260801", reason="需要换货", type="exchange"
        )
        assert result.success is True
        assert result.data["type"] == "exchange"
        assert result.data["type_label"] == "换货"

    asyncio.run(scenario())


def test_tool_contract_validation_idempotency_and_permission_failure():
    async def scenario():
        query = get_tool("query_order")
        missing = await query.execute_with_retry()
        assert missing.success is False
        assert missing.error_type == "validation"

        forbidden = await query.execute_with_retry(order_id="ORD20260801", user_id=2)
        assert forbidden.success is False
        assert forbidden.error_type == "permission"

        refund = get_tool("apply_refund")
        first = await refund.execute_with_retry(
            order_id="ORD20260801", reason="重复提交测试", type="refund",
            user_id=1, idempotency_key="contract-refund-1",
        )
        second = await refund.execute_with_retry(
            order_id="ORD20260801", reason="重复提交测试", type="refund",
            user_id=1, idempotency_key="contract-refund-1",
        )
        assert first.success and second.success
        assert first.data["refund_id"] == second.data["refund_id"]

    asyncio.run(scenario())


def test_tool_timeout_is_classified_and_retried_with_backoff():
    class SlowTool(BaseTool):
        name = "slow_contract_tool"
        timeout_ms = 5
        max_retries = 2
        parameters = []

        async def execute(self, **kwargs):
            await asyncio.sleep(0.05)
            return {"success": True, "data": {}}

    async def scenario():
        result = await SlowTool().execute_with_retry()
        assert result.success is False
        assert result.error_type == "timeout"
        assert result.retry_count == 2

    asyncio.run(scenario())
