"""路由冲突回归：咨询、当前动作与历史背景不能相互授权。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.graph import AgentGraph
from app.agent.intent import EnhancedIntentRecognizer
from app.agent.state import AgentNode, AgentState
from tests.test_m4_understanding import result as understanding


@pytest.mark.parametrize("topic,message", [
    ("refund", "ORD940001退款多久到账"),
    ("refund", "ORD940001可以退吗"),
    ("refund", "申请退款需要什么材料"),
    ("refund", "不要申请退款，只问流程"),
    ("complaint", "提交投诉需要什么材料"),
    ("complaint", "不要提交投诉，只问处理进度"),
])
async def test_inquiry_precedes_slots_even_with_current_or_saved_order(topic, message):
    graph = object.__new__(AgentGraph)
    state = AgentState(session_id="s", user_id=101, user_message=message,
                       detected_intent=topic, collected_info={"order_id": "ORD940001"})
    assert await graph._node_route_decision(state) == AgentNode.QUERY_REWRITE
    assert not state.missing_slots and not state.pending_confirmations
    assert not state.tool_calls


@pytest.mark.parametrize("topic,message", [
    ("refund", "我要退钱，退货流程是？"),
    ("refund", "不想转人工，请帮我申请退款，商品外包装破损严重"),
    ("refund", "不要退款只问政策，但现在请申请退款，商品外包装破损严重"),
    ("complaint", "不要退款，我要提交投诉，了解一下处理流程"),
])
async def test_explicit_action_is_not_hidden_by_other_inquiry_or_negation(topic, message):
    graph = object.__new__(AgentGraph)
    state = AgentState(session_id="s", user_id=101, user_message=message,
                       detected_intent=topic,
                       collected_info={"order_id": "ORD940001", "reason": "商品外包装破损严重"})
    assert await graph._node_route_decision(state) == AgentNode.TOOL_EXECUTION
    # 这里只决定目标；确认和执行仍必须由原工具节点独立检查。
    assert not state.tool_calls and not state.pending_confirmations


async def test_conditional_refund_in_rules_mode_cannot_prepare_action():
    graph = object.__new__(AgentGraph)
    state = AgentState(session_id="s", user_id=101,
                       user_message="如果没发货就帮我申请退款", detected_intent="refund",
                       collected_info={"order_id": "ORD940001", "reason": "商品外包装破损严重"})
    assert await graph._node_route_decision(state) == AgentNode.RESPONSE_GENERATION
    assert state.needs_clarification and not state.tool_calls
    assert not state.pending_confirmations and not state.refund_preview_data()


async def test_validated_model_action_is_not_reclassified_by_legacy_keywords():
    from app.agent.request_understanding import route_understanding, validate_semantics

    graph = object.__new__(AgentGraph)
    state = AgentState(session_id="s", user_id=101, user_message="ORD940001按售后方案办理，商品外包装破损严重",
                       collected_info={"reason": "商品外包装破损严重"})
    value = understanding(request_type="apply", entity_source="current_message",
                          entities={"order_id": "ORD940001", "product": None})
    assert route_understanding(state, validate_semantics(value, state)) == AgentNode.ROUTE_DECISION
    assert await graph._node_route_decision(state) == AgentNode.TOOL_EXECUTION


def test_new_question_does_not_inherit_old_tool_intent():
    recognizer = EnhancedIntentRecognizer()
    context = [{"role": "tool", "tool_name": "apply_refund", "content": "历史回执"}]
    result = recognizer.recognize("北京明天天气怎么样", context=context, history_intent="refund")
    assert result.intent == "general"
    assert result.needs_llm_fallback


async def test_inquiry_complete_graph_never_prepares_or_executes_refund(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    prepare = AsyncMock(side_effect=AssertionError("咨询不得生成退款预览"))
    invoke = AsyncMock(side_effect=AssertionError("咨询不得执行业务工具"))
    monkeypatch.setattr("app.agent.nodes.tool_node.invoke_tool", invoke)
    monkeypatch.setattr("app.agent.nodes.tool_node.persistent_actions", lambda: SimpleNamespace(prepare=prepare))
    graph = AgentGraph()
    original_model = graph.llm_service
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    search = AsyncMock(return_value={"success": True, "results": [], "meta": {}})
    monkeypatch.setattr("app.agent.graph.get_knowledge_base", lambda: SimpleNamespace(async_search=search))
    try:
        state = await graph.run(AgentState(
            session_id="inquiry-route", user_id=101, user_message="ORD940001退款多久到账",
            collected_info={"reason": "商品外包装破损严重"}, understanding_mode="rules"))
        assert state.current_node == AgentNode.END
        assert search.await_count == 1
        assert all(call.tool_name == "hybrid_search" for call in state.tool_calls)
        assert not state.pending_confirmations and not state.refund_preview_data()
        prepare.assert_not_awaited()
        invoke.assert_not_awaited()
    finally:
        await original_model.close()


@pytest.mark.parametrize("topic,message", [
    ("human", "不要转人工，只问问题"),
    ("human", "不需要联系真人客服"),
])
async def test_negated_handoff_does_not_create_request(topic, message):
    graph = object.__new__(AgentGraph)
    state = AgentState(session_id="s", user_id=101, user_message=message, detected_intent=topic)
    assert await graph._node_route_decision(state) == AgentNode.RESPONSE_GENERATION
    assert state.needs_clarification and not state.tool_calls


@pytest.mark.parametrize("message", [
    "我想了解ORD940001的退款流程", "ORD940001退款申请需要什么材料", "ORD940001退货运费谁出",
])
async def test_consulting_clears_stale_missing_slots(message):
    graph = object.__new__(AgentGraph)
    state = AgentState(session_id="s", user_id=101, user_message=message, detected_intent="refund",
                       missing_slots=["reason"], collected_info={"order_id": "ORD940001"})
    assert await graph._node_route_decision(state) == AgentNode.QUERY_REWRITE
    assert state.missing_slots == []
