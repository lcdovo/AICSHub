from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
import pytest
from app.agent.state import AgentState, AgentNode
from app.agent.nodes.tool_node import ConfirmationNode, ConfirmationDependencies, is_refund_confirmed


def receipt():
    return {"action_id": "synthetic-action", "version": 1, "order_id": "ORD950020",
            "type": "refund", "reason": "合成商品严重破损", "amount": "0.10", "currency": "CNY",
            "status": "pending", "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "application_id": None, "is_simulated": True}


def preview():
    return {"action_id": "synthetic-action", "action_version": 1, "order_id": "ORD950020",
            "type": "refund", "reason": "合成商品严重破损", "user_id": 101, "session_id": "s"}


@pytest.mark.parametrize("changes", [
    {"amount": float("nan")}, {"amount": "NaN"}, {"amount": "0.001"}, {"amount": "0.00"}, {"amount": "０.１０"},
    {"currency": "USD"}, {"version": True}, {"is_simulated": False}, {"expires_at": "not-a-date"},
    {"authorization": "synthetic-private"},
])
async def test_invalid_action_receipt_cannot_be_shown_as_a_confirmable_quote(changes):
    state = AgentState(session_id="s", user_id=101, user_message="只看一下预览",
                       collected_info={"refund_preview": preview(), "refund_action": {**receipt(), **changes}})
    pause = Mock(side_effect=AssertionError("invalid receipt reached confirmation"))
    node = ConfirmationNode(ConfirmationDependencies(is_refund_confirmed, pause))
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.needs_clarification and state.refund_action_error
    assert "模拟金额" not in state.reply and "synthetic-private" not in state.reply
    pause.assert_not_called()


@pytest.mark.parametrize("action", [{"action_id": "synthetic-action", "version": 1}, None, "not-an-object"])
async def test_partial_receipt_does_not_crash_or_ask_for_confirmation_without_amount(action):
    state = AgentState(session_id="s", user_id=101, user_message="展示预览",
                       collected_info={"refund_preview": preview(), "refund_action": action})
    node = ConfirmationNode(ConfirmationDependencies(is_refund_confirmed, lambda _: False))
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.needs_clarification and "模拟金额" not in state.reply


@pytest.mark.parametrize("changes", [{"status": "submitted"}, {"status": "cancelled"},
                                      {"expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}])
async def test_inactive_action_does_not_ask_for_new_confirmation(changes):
    state = AgentState(session_id="s", user_id=101, user_message="展示预览",
                       collected_info={"refund_preview": preview(), "refund_action": {**receipt(), **changes}})
    pause = Mock(side_effect=AssertionError("inactive action reached confirmation"))
    node = ConfirmationNode(ConfirmationDependencies(is_refund_confirmed, pause))
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.needs_clarification
    pause.assert_not_called()


async def test_action_quote_must_match_current_preview_before_display():
    state = AgentState(session_id="s", user_id=101, user_message="展示预览",
                       collected_info={"refund_preview": preview(), "refund_action": {**receipt(), "order_id": "ORD950021"}})
    node = ConfirmationNode(ConfirmationDependencies(is_refund_confirmed, lambda _: False))
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.needs_clarification and "模拟金额" not in state.reply


async def test_valid_exact_amount_is_preserved_and_mirror_mutation_invalidates_confirmation():
    state = AgentState(session_id="s", user_id=101, user_message="展示预览")
    state.set_refund_preview(preview())
    assert state.record_refund_action(receipt())
    node = ConfirmationNode(ConfirmationDependencies(is_refund_confirmed, lambda _: True))
    assert await node(state) == AgentNode.TOOL_EXECUTION
    assert "0.10 CNY" in state.reply and state.has_current_refund_confirmation()
    state.collected_info["refund_action"]["amount"] = "100.00"
    assert not state.has_current_refund_confirmation()
    assert state.refund_action_view.amount == "0.10"
    assert state.refund_action_error == "refund_action_projection_changed"


@pytest.mark.parametrize("returned", [{"action_id": "synthetic-action", "version": 1}, None, "not-an-object"])
async def test_prepare_returning_only_id_and_version_is_explicit_unknown_not_confirmable_success(returned):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.agent.nodes.tool_node import ToolExecutionNode, ToolExecutionDependencies, extract_info, select_tools

    actions = SimpleNamespace(prepare=AsyncMock(return_value=returned),
                              pending_count=AsyncMock())
    invoke = AsyncMock(side_effect=AssertionError("partial prepare receipt must not submit"))
    node = ToolExecutionNode(ToolExecutionDependencies(extract_info, select_tools, is_refund_confirmed, invoke, actions))
    state = AgentState(session_id="s", user_id=101, actor_id=101, source_message_id=7,
                       user_message="订单ORD950020商品严重破损，请申请退款", detected_intent="refund")
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.needs_clarification and state.refund_action_error == "refund_action_invalid"
    assert not state.pending_confirmations and not state.refund_preview_data()
    invoke.assert_not_awaited()
