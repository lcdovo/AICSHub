from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.agent.state import AgentState, AgentNode
from app.agent.nodes.tool_node import ToolExecutionNode, ToolExecutionDependencies, extract_info, select_tools, is_refund_confirmed


@pytest.mark.parametrize("preview", [
    "not-an-object", {"order_id": "ORD950018", "reason": 12345},
    {"order_id": "ORD950018", "user_id": 202},
    {"order_id": "ORD950018", "session_id": "other"},
    {"order_id": "ORD950018", "action_version": True},
    {"order_id": "ORD950018", "authorization": "synthetic-private"},
])
async def test_malformed_or_foreign_preview_clarifies_before_any_tool_call(preview):
    invoke = AsyncMock(side_effect=AssertionError("invalid preview reached invocation"))
    actions = SimpleNamespace(prepare=AsyncMock(), pending_count=AsyncMock(return_value=1))
    node = ToolExecutionNode(ToolExecutionDependencies(extract_info, select_tools, is_refund_confirmed, invoke, actions))
    state = AgentState(session_id="s", user_id=101, actor_id=101, source_message_id=7,
                       user_message="确认", detected_intent="refund", collected_info={
                           "order_id": "ORD950018", "reason": "合成商品严重破损", "refund_type": "refund",
                           "refund_preview": preview})
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.needs_clarification and state.refund_preview_error
    assert not state.tool_calls and "synthetic-private" not in state.reply
    invoke.assert_not_awaited()
    actions.prepare.assert_not_awaited()


def test_preview_is_copied_and_projection_change_invalidates_confirmation_without_changing_typed_parameters():
    state = AgentState(session_id="s", user_id=101, actor_id=101, source_message_id=7,
                       user_message="合成待确认请求")
    preview = {"order_id": "ORD950018", "reason": "合成原因", "type": "refund",
               "user_id": 101, "session_id": "s", "action_id": "synthetic", "action_version": 1}
    state.set_refund_preview(preview)
    state.record_refund_confirmation("interrupt")
    assert state.has_current_refund_confirmation()
    preview["order_id"] = "ORD950019"
    assert state.refund_preview_data()["order_id"] == "ORD950018"
    state.collected_info["refund_preview"]["action_version"] = 2
    assert not state.has_current_refund_confirmation()
    assert state.refund_preview.action_version == 1
    assert state.refund_preview_error == "refund_preview_projection_changed"


def test_explicit_clear_cannot_be_repopulated_from_legacy_dictionary_and_next_turn_starts_clean():
    state = AgentState(session_id="s", user_id=101, user_message="合成请求")
    state.set_refund_preview({"order_id": "ORD950018"})
    state.set_refund_preview(None)
    state.collected_info["refund_preview"] = {"action_id": "stale"}
    assert state.refund_preview_data() == {}
    fresh = state.for_next_turn(101, "新问题")
    assert fresh.refund_preview is None and "refund_preview" not in fresh.collected_info


def test_incomplete_legacy_preview_can_be_display_context_without_granting_confirmation():
    state = AgentState(session_id="s", user_id=101, user_message="只看一下",
                       collected_info={"refund_preview": {"order_id": "ORD950018"}})
    assert state.refund_preview_data() == {"order_id": "ORD950018"}
    assert state.refund_preview.action_id is None
    assert not is_refund_confirmed(state)


def test_next_turn_does_not_normalize_or_modify_previous_state_object():
    state = AgentState(session_id="s", user_id=101, user_message="旧请求",
                       pending_confirmations=["apply_refund"], collected_info={
                           "order_id": "ORD950018", "refund_preview": {"order_id": "ORD950018"},
                           "confirm_refund": True})
    before = state.model_dump()
    fresh = state.for_next_turn(101, "新消息")
    assert state.model_dump() == before
    assert fresh.refund_preview_data()["order_id"] == "ORD950018"
    assert "confirm_refund" not in fresh.collected_info


def test_invalid_pending_legacy_preview_does_not_become_next_turn_pending_action():
    state = AgentState(session_id="s", user_id=101, user_message="旧请求",
                       pending_confirmations=["apply_refund"], collected_info={"refund_preview": "invalid"})
    before = state.model_dump()
    fresh = state.for_next_turn(101, "新消息")
    assert not fresh.pending_confirmations and not fresh.refund_preview_data()
    assert state.model_dump() == before


@pytest.mark.parametrize("changed", ["user", "session"])
def test_established_preview_is_not_exposed_when_current_scope_changes(changed):
    state = AgentState(session_id="s", user_id=101, user_message="合成请求")
    state.set_refund_preview({"order_id": "ORD950018", "user_id": 101, "session_id": "s"})
    if changed == "user":
        state.user_id = 202
    else:
        state.session_id = "other"
    assert state.refund_preview_data() == {}
    assert state.refund_preview_error == "refund_preview_scope_changed"
    assert isinstance(state.business_context(), dict)
    assert "refund_preview" not in state.business_context()
