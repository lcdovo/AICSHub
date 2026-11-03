from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from app.agent.state import AgentState, AgentNode
from app.agent.nodes.tool_node import ToolExecutionNode, ToolExecutionDependencies, select_tools, is_refund_confirmed
from app.agent.nodes.intent_node import required_slots
from app.agent.request_understanding import input_payload
from app.agent.tools import ToolExecutionResult


@pytest.mark.parametrize("invalid", [
    {"order_id": True}, {"order_id": "X" * 33}, {"reason": 12345678},
    {"refund_type": "delete"}, {"complaint_content": {"content": "bad shape"}},
    {"confirm_refund": True}, {"user_id": 202},
])
def test_business_parameters_are_strict_and_updates_do_not_partially_apply(invalid):
    state = AgentState(session_id="s", user_id=101, user_message="合成问题")
    state.set_business_parameters(order_id="ORD950013", reason="合成商品破损原因", refund_type="refund")
    previous = state.business_parameters
    with pytest.raises(ValidationError):
        state.set_business_parameters(**invalid)
    assert state.business_parameters is previous
    assert state.collected_info["order_id"] == "ORD950013"


async def test_typed_order_used_for_tool_prompt_and_next_turn_despite_changed_compatibility_projection():
    import json
    state = AgentState(session_id="s", user_id=101, actor_id=101,
                       user_message="查当前订单", detected_intent="query_order")
    state.set_business_parameters(order_id="ORD950013")
    state.collected_info["order_id"] = "ORD950014"
    invoke = AsyncMock(return_value=ToolExecutionResult(success=True, data={"order_id": "ORD950013"}))
    tool = ToolExecutionNode(ToolExecutionDependencies(lambda _: {}, select_tools,
                                                      is_refund_confirmed, invoke, None))
    assert await tool(state) == AgentNode.RESULT_VERIFICATION
    assert invoke.call_args.kwargs["order_id"] == "ORD950013"
    assert json.loads(input_payload(state))["known_order"] == "ORD950013"
    fresh = state.for_next_turn(101, "新问题")
    assert fresh.current_business_parameters().order_id == "ORD950013"
    assert fresh.business_parameters is not state.business_parameters


async def test_extractor_cannot_install_unregistered_control_fields_or_identity():
    invoke = AsyncMock(side_effect=AssertionError("invalid extraction must never reach tool"))
    state = AgentState(session_id="s", user_id=101, actor_id=101, user_message="查订单", detected_intent="query_order")
    tool = ToolExecutionNode(ToolExecutionDependencies(
        lambda _: {"order_id": "ORD950013", "confirm_refund": True, "user_id": 202},
        select_tools, is_refund_confirmed, invoke, None))
    assert await tool(state) == AgentNode.RESPONSE_GENERATION
    assert state.needs_clarification and "confirm_refund" not in state.collected_info
    assert state.collected_info["user_id"] == 101
    invoke.assert_not_awaited()


async def test_invalid_legacy_slot_cannot_be_silently_coerced_into_a_tool_request():
    invoke = AsyncMock(side_effect=AssertionError("invalid legacy slot must not execute"))
    state = AgentState(session_id="s", user_id=101, actor_id=101, user_message="查当前订单",
                       detected_intent="query_order", collected_info={"order_id": 12345678})
    tool = ToolExecutionNode(ToolExecutionDependencies(lambda _: {}, select_tools,
                                                      is_refund_confirmed, invoke, None))
    assert await tool(state) == AgentNode.RESPONSE_GENERATION
    assert state.business_parameter_errors == ["order_id"] and state.business_parameters.order_id is None
    assert "order_id" not in state.collected_info
    invoke.assert_not_awaited()
    state.set_business_parameters(order_id="ORD950013")
    assert state.business_parameter_errors == [] and required_slots(state) == []


def test_short_reason_is_allowed_as_incomplete_parameter_but_existing_six_character_slot_policy_is_kept():
    state = AgentState(session_id="s", user_id=101, user_message="合成问题", detected_intent="refund")
    state.set_business_parameters(order_id="ORD950013", reason="破损", refund_type="refund")
    assert required_slots(state) == ["reason"]
    state.set_business_parameters(reason="合成商品严重破损")
    assert required_slots(state) == []


def test_explicitly_cleared_order_is_not_restored_from_old_projection():
    state = AgentState(session_id="s", user_id=101, user_message="换一个订单")
    state.set_business_parameters(order_id=None)
    state.collected_info["order_id"] = "ORD950014"
    assert state.current_business_parameters().order_id is None
    assert "order_id" not in state.business_context()


def test_unfinished_parameters_survive_roundtrip_while_finished_reason_is_not_carried_forward():
    state = AgentState(session_id="s", user_id=101, user_message="合成问题", missing_slots=["reason"])
    state.set_business_parameters(order_id="ORD950013", reason="破损", refund_type="exchange")
    restored = AgentState.model_validate(state.model_dump())
    fresh = restored.for_next_turn(101, "补充新原因")
    assert fresh.current_business_parameters().reason == "破损"
    assert fresh.current_business_parameters().refund_type == "exchange"
    restored.missing_slots = []
    finished = restored.for_next_turn(101, "新问题")
    assert finished.current_business_parameters().reason is None
    assert finished.current_business_parameters().order_id == "ORD950013"


def test_model_order_must_fit_actual_domain_order_contract_before_routing():
    from app.agent.request_understanding import validate_semantics
    from tests.test_m4_understanding import result
    order = "ORD" + "1" * 30
    state = AgentState(session_id="s", user_id=101, user_message=f"查询{order}")
    value = result(topic="query_order", request_type="query", entities={"order_id": order, "product": None})
    with pytest.raises(ValidationError):
        validate_semantics(value, state)
    assert not state.tool_calls and not state.pending_confirmations
