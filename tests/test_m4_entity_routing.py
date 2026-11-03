from unittest.mock import AsyncMock
import pytest
from app.agent.state import AgentState, AgentNode
from app.agent.graph import AgentGraph
from app.agent.request_understanding import RequestUnderstanding, validate_semantics, route_understanding
from app.agent.nodes.intent_node import route_decision_node
from app.agent.nodes.tool_node import tool_execution_node
from app.agent.tools import ToolExecutionResult


def understanding(**changes):
    fields = dict(topic="query_order", request_type="query",
                  entities={"order_id": "ORD950007", "product": None},
                  conditions=[], subrequests=[], service_preference="unspecified", emotion="neutral",
                  clarification=None, retrieval_query="查订单ORD950007", entity_source="current_message")
    fields.update(changes)
    return RequestUnderstanding.model_validate(fields)


async def test_model_selected_order_is_not_overwritten_by_first_literal(monkeypatch):
    graph = AgentGraph()
    state = AgentState(session_id="s", user_id=101, actor_id=101,
                       user_message="不要查ORD950006，只查询ORD950007的当前状态")
    value = validate_semantics(understanding(), state)
    route_understanding(state, value)
    try:
        await route_decision_node(graph, state)
        assert state.collected_info["order_id"] == "ORD950007"
        invoke = AsyncMock(return_value=ToolExecutionResult(
            success=True, data={"order_id": "ORD950007", "status": "已发货"}))
        monkeypatch.setattr("app.agent.nodes.tool_node.invoke_tool", invoke)
        await tool_execution_node(graph, state)
        assert invoke.call_args.kwargs["order_id"] == "ORD950007"
    finally:
        await graph.llm_service.close()


@pytest.mark.parametrize("topic,request_type", [("query_order", "query"), ("refund", "apply")])
async def test_unresolved_model_order_cannot_reuse_previous_turn_order(topic, request_type):
    graph = AgentGraph()
    state = AgentState(session_id="s", user_id=101, user_message="给另一个订单办理一下",
                       collected_info={"order_id": "ORD950006", "reason": "旧参数"})
    value = understanding(topic=topic, request_type=request_type,
                          entities={"order_id": None, "product": None}, entity_source="none")
    route_understanding(state, value)
    try:
        assert "order_id" not in state.collected_info
        assert await route_decision_node(graph, state) == AgentNode.SLOT_FILLING
        assert "order_id" in state.missing_slots and not state.tool_calls
    finally:
        await graph.llm_service.close()


@pytest.mark.parametrize("topic,message", [
    ("human", "不要退款，请转人工"),
    ("complaint", "不想退货，我要正式投诉客服态度"),
])
def test_negated_refund_does_not_veto_a_different_explicit_request(topic, message):
    state = AgentState(session_id="s", user_id=101, user_message=message)
    value = understanding(topic=topic, request_type="apply", entities={"order_id": None, "product": None},
                          entity_source="none")
    assert validate_semantics(value, state) is value


@pytest.mark.parametrize("message", ["不要退款", "不想退货", "不是申请，只问政策"])
def test_negated_refund_still_cannot_create_refund_intent(message):
    state = AgentState(session_id="s", user_id=101, user_message=message)
    with pytest.raises(ValueError, match="negated_action"):
        validate_semantics(understanding(topic="refund", request_type="apply",
                                         entities={"order_id": None, "product": None}, entity_source="none"), state)


@pytest.mark.parametrize("topic,message", [
    ("human", "不要转人工，只想了解政策"),
    ("human", "不需要联系真人客服"),
    ("complaint", "不要创建投诉，我只咨询保修"),
    ("complaint", "不发起投诉，只查处理进度"),
])
def test_each_action_checks_its_own_negation(topic, message):
    with pytest.raises(ValueError, match="negated_action"):
        validate_semantics(understanding(topic=topic, request_type="apply",
                                         entities={"order_id": None, "product": None}, entity_source="none"),
                           AgentState(session_id="s", user_id=101, user_message=message))


@pytest.mark.parametrize("topic,message", [
    ("refund", "不要转人工，请申请退款"),
    ("refund", "不要创建投诉，请申请退款"),
])
def test_other_negated_action_does_not_veto_refund(topic, message):
    value = understanding(topic=topic, request_type="apply", entities={"order_id": None, "product": None},
                          entity_source="none")
    assert validate_semantics(value, AgentState(session_id="s", user_id=101, user_message=message)) is value
