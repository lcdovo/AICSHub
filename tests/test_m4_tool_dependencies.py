from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from app.agent.state import AgentState, AgentNode
from app.agent.nodes.tool_node import (
    ToolExecutionNode, ToolExecutionDependencies, ConfirmationNode, ConfirmationDependencies,
    extract_info, select_tools, is_refund_confirmed, verify_tool_result,
)
from app.agent.tools import ToolExecutionResult
from app.models.models import RefundAction
from app.services.refund_actions import RefundActionCoordinator
from tests.test_ticket_idempotency import api_context


async def test_tool_node_runs_only_injected_boundaries_and_keeps_live_user_scope():
    invoke = AsyncMock(return_value=ToolExecutionResult(success=True, data={"order_id": "ORD950008"}))
    actions = SimpleNamespace(prepare=AsyncMock(), pending_count=AsyncMock())
    node = ToolExecutionNode(ToolExecutionDependencies(extract_info, select_tools,
                                                      is_refund_confirmed, invoke, actions))
    state = AgentState(session_id="s", user_id=101, actor_id=101, user_message="查订单ORD950008",
                       detected_intent="query_order", collected_info={"user_id": 202})
    assert await node(state) == AgentNode.RESULT_VERIFICATION
    assert invoke.call_args.kwargs["user_id"] == 101
    assert invoke.call_args.kwargs["order_id"] == "ORD950008"
    assert state.tool_calls[0].arguments["user_id"] == 101
    assert "authorization" not in state.tool_calls[0].arguments
    actions.prepare.assert_not_awaited()


async def test_preview_prepares_persistent_action_without_invoking_write_tool():
    def prepared(actor_id, payload, previous):
        return {"action_id": "synthetic-action", "version": 1, "order_id": payload["order_id"],
                "type": payload["type"], "reason": payload["reason"], "amount": "0.10", "currency": "CNY",
                "status": "pending", "expires_at": (datetime.utcnow() + timedelta(minutes=5)).isoformat(),
                "application_id": None, "is_simulated": True}

    actions = SimpleNamespace(prepare=AsyncMock(side_effect=prepared),
                              pending_count=AsyncMock())
    invoke = AsyncMock(side_effect=AssertionError("preview must not submit"))
    node = ToolExecutionNode(ToolExecutionDependencies(extract_info, select_tools,
                                                      is_refund_confirmed, invoke, actions))
    state = AgentState(session_id="s", user_id=101, actor_id=101, source_message_id=7,
                       user_message="订单ORD950008商品破损，请申请退款", detected_intent="refund")
    assert await node(state) == AgentNode.CONFIRMATION
    assert actions.prepare.call_args.args[0] == 101
    assert actions.prepare.call_args.args[1]["session_id"] == "s"
    assert state.pending_confirmations == ["apply_refund"]
    assert state.collected_info["refund_preview"]["action_id"] == "synthetic-action"
    invoke.assert_not_awaited()


async def test_multiple_persistent_actions_do_not_execute_implicit_confirmation():
    actions = SimpleNamespace(prepare=AsyncMock(), pending_count=AsyncMock(return_value=2))
    invoke = AsyncMock(side_effect=AssertionError("ambiguous confirmation must not submit"))
    node = ToolExecutionNode(ToolExecutionDependencies(extract_info, select_tools,
                                                      is_refund_confirmed, invoke, actions))
    state = AgentState(session_id="s", user_id=101, actor_id=101, source_message_id=7,
                       user_message="确认", detected_intent="refund",
                       collected_info={"order_id": "ORD950008", "refund_preview": {"action_id": "old"}})
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.needs_clarification and "action_id" in state.reply
    actions.pending_count.assert_awaited_once_with(101, "s")
    invoke.assert_not_awaited()


async def test_confirmation_and_verification_need_no_graph_private_helpers():
    pauses = []
    node = ConfirmationNode(ConfirmationDependencies(is_refund_confirmed,
                                                     lambda data: pauses.append(data) or False))
    state = AgentState(session_id="s", user_id=101, user_message="先看预览",
                       collected_info={"refund_preview": {"order_id": "ORD950008", "reason": "合成商品破损"}})
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert pauses[0]["order_id"] == "ORD950008"
    state.user_message = "确认"
    assert await node(state) == AgentNode.TOOL_EXECUTION
    assert len(pauses) == 1
    assert await verify_tool_result(state) == AgentNode.RESPONSE_GENERATION


async def test_domain_pending_count_filters_owner_session_terminal_and_expiry(tmp_path):
    async with api_context(tmp_path / "pending.db") as (_, factory):
        now = datetime.utcnow()
        async with factory() as db:
            for name, owner, session, status, expiry in [
                ("valid", 101, "session-101", "pending", now + timedelta(minutes=5)),
                ("expired", 101, "session-101", "pending", now - timedelta(seconds=1)),
                ("cancelled", 101, "session-101", "cancelled", now + timedelta(minutes=5)),
                ("other-owner", 202, "session-202", "pending", now + timedelta(minutes=5)),
            ]:
                db.add(RefundAction(id=name, user_id=owner, actor_id=owner, session_id=session,
                                    order_id="SYNTHETIC", type="refund", reason="合成原因",
                                    amount="0.10", currency="CNY", status=status, expires_at=expiry))
            await db.commit()
        actions = RefundActionCoordinator(lambda: factory)
        assert await actions.pending_count(101, "session-101") == 1
        assert await actions.pending_count(101, "session-202") == 0
        assert await actions.pending_count(202, "session-202") == 1
