from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from app.agent.intent import EnhancedIntentRecognizer
from app.agent.state import AgentState, AgentNode
from app.agent.nodes.intent_node import (
    IntentNode, IntentDependencies, RoutingNode, RoutingDependencies,
    ClarificationNode, ClarificationDependencies, detect_escalation,
    fill_slots,
)
from app.agent.nodes.tool_node import is_refund_confirmed, refund_cancelled, extract_info
from app.agent.nodes.response_node import is_smalltalk
from tests.test_m4_understanding import result as understanding


def dependencies(model=None, actions=None):
    return IntentDependencies(
        EnhancedIntentRecognizer(), lambda: model, is_refund_confirmed, refund_cancelled,
        is_smalltalk, detect_escalation, actions or SimpleNamespace(cancel=AsyncMock()),
    )


async def test_pending_confirmation_and_cancel_do_not_resolve_model_service():
    actions = SimpleNamespace(cancel=AsyncMock(return_value={"status": "cancelled"}))
    original = dependencies(actions=actions)
    from dataclasses import replace
    factory = Mock(side_effect=AssertionError("current confirmation must not call model"))
    node = IntentNode(replace(original, model_factory=factory))
    for message in ["确认", "取消"]:
        state = AgentState(session_id="s", user_id=101, actor_id=999, user_message=message,
                           collected_info={"refund_preview": {"action_id": "synthetic-action"}})
        destination = await node(state)
        assert destination == (AgentNode.ROUTE_DECISION if message == "确认" else AgentNode.RESPONSE_GENERATION)
    factory.assert_not_called()
    actions.cancel.assert_awaited_once_with(999, 101, "synthetic-action")


async def test_shadow_does_not_cancel_or_confirm_action_through_injected_service():
    model = SimpleNamespace(run_structured=AsyncMock(return_value=None), is_available=lambda: False)
    actions = SimpleNamespace(cancel=AsyncMock(side_effect=AssertionError("shadow write")))
    node = IntentNode(dependencies(model, actions))
    state = AgentState(session_id="s", user_id=101, user_message="取消", understanding_mode="shadow",
                       collected_info={"refund_preview": {"action_id": "keep"}})
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.collected_info["refund_preview"]["action_id"] == "keep"
    actions.cancel.assert_not_awaited()


async def test_model_clarification_and_routing_use_only_declared_components():
    model = SimpleNamespace(run_structured=AsyncMock(return_value=understanding()), is_available=lambda: True)
    node = IntentNode(dependencies(model))
    state = AgentState(session_id="s", user_id=101, user_message="耳机ORD940001退款多久到账",
                       understanding_mode="model")
    assert await node(state) == AgentNode.RAG_RETRIEVAL
    assert state.understanding and not state.pending_confirmations
    route = RoutingNode(RoutingDependencies(extract_info))
    missing = AgentState(session_id="s", user_id=101, user_message="查订单", detected_intent="query_order")
    assert await route(missing) == AgentNode.SLOT_FILLING
    assert await fill_slots(missing) == AgentNode.RESPONSE_GENERATION
    assert "订单号" in missing.reply
    clarifier = ClarificationNode(ClarificationDependencies(EnhancedIntentRecognizer()))
    assert await clarifier(missing) == AgentNode.RESPONSE_GENERATION
    assert missing.needs_clarification


async def test_main_graph_resolves_replaced_model_service_via_injected_factory(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent.graph import AgentGraph

    graph = AgentGraph()
    original_service = graph.llm_service
    replacement = SimpleNamespace(run_structured=AsyncMock(return_value=understanding()), is_available=lambda: True)
    graph.llm_service = replacement
    monkeypatch.setattr("app.agent.graph.get_knowledge_base", lambda: SimpleNamespace(
        async_search=AsyncMock(return_value={"success": True, "results": [], "meta": {}})))
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    try:
        state = await graph.run(AgentState(session_id="s", user_id=101,
                                           user_message="耳机ORD940001退款多久到账", understanding_mode="model"))
        replacement.run_structured.assert_awaited_once()
        assert state.current_node == AgentNode.END
        assert not state.tool_calls or all(t.tool_name == "hybrid_search" for t in state.tool_calls)
    finally:
        await original_service.close()
