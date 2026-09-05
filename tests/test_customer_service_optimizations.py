import asyncio

from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.agent.validation import ValidationResult


def test_chat_agent_reads_admin_knowledge_base_and_adds_citations(monkeypatch):
    class KnowledgeBase:
        def search(self, **kwargs):
            return {"success": True, "results": [{
                "title": "admin-policy", "content": "uploaded logistics rule", "category": "help", "final_score": 0.91,
            }]}

    class PassingValidator:
        def validate(self, **kwargs):
            return ValidationResult()

    monkeypatch.setattr("app.agent.graph.get_knowledge_base", lambda: KnowledgeBase())
    graph = AgentGraph()
    graph.validator = PassingValidator()
    state = AgentState(session_id="rag-shared", user_id=1, user_message="logistics")
    state.detected_intent = "technical"

    async def run():
        await graph._node_rag_retrieval(state)
        await graph._node_response_generation(state)
        return state

    state = asyncio.run(run())
    assert state.collected_info["kb_results"][0]["title"] == "admin-policy"
    assert "\u53c2\u8003\u6765\u6e90" in state.reply
    assert "admin-policy" in state.reply


def test_follow_up_keeps_order_id_and_queries_same_order():
    async def scenario():
        graph = AgentGraph()
        first = await graph.run(AgentState(
            session_id="multi-turn", user_id=1, user_message="\u67e5\u8be2\u8ba2\u5355 ORD20260801"
        ))
        second = AgentState(
            session_id="multi-turn", user_id=1, user_message="\u7269\u6d41\u600e\u4e48\u6837",
            messages=first.messages, collected_info=first.collected_info,
            detected_intent=first.detected_intent,
        )
        second = await graph.run(second)
        return first, second

    first, second = asyncio.run(scenario())
    assert first.collected_info["order_id"] == "ORD20260801"
    assert second.collected_info["order_id"] == "ORD20260801"
    assert any(call.tool_name == "query_order" and call.success for call in second.tool_calls)
    assert [m["role"] for m in first.messages].count("assistant") >= 1


def test_response_is_validated_after_generation():
    class RecordingValidator:
        def __init__(self):
            self.responses = []

        def validate(self, **kwargs):
            self.responses.append(kwargs["response"])
            return ValidationResult()

    async def scenario():
        graph = AgentGraph()
        validator = RecordingValidator()
        graph.validator = validator

        async def generate(_state):
            return "generated final response"

        graph._generate_response_with_llm = generate
        state = AgentState(session_id="validation", user_id=1, user_message="hello there")
        await graph.run(state)
        return validator.responses

    assert asyncio.run(scenario()) == ["generated final response"]


def test_order_query_without_order_id_uses_slot_filling_node():
    async def scenario():
        graph = AgentGraph()
        return await graph.run(AgentState(
            session_id="slot-filling-order",
            user_id=1,
            user_message="请查询物流状态",
        ))

    state = asyncio.run(scenario())
    assert "order_id" in state.missing_slots
    assert any(trace["node"] == "route_decision" for trace in state.trace)
    assert any(trace["node"] == "slot_filling" for trace in state.trace)
    assert not any(call.tool_name == "query_order" for call in state.tool_calls)
    assert "订单号" in state.reply


def test_low_confidence_rag_sets_safe_fallback_without_llm():
    class LowConfidenceKnowledgeBase:
        async def async_search(self, **kwargs):
            return {
                "success": True,
                "results": [{"title": "weak", "content": "unrelated", "final_score": 0.1}],
                "meta": {"backend": "memory"},
            }

    async def scenario():
        graph = AgentGraph()
        graph_knowledge_base = LowConfidenceKnowledgeBase()
        graph_module = __import__("app.agent.graph", fromlist=["get_knowledge_base"])
        original = graph_module.get_knowledge_base
        graph_module.get_knowledge_base = lambda: graph_knowledge_base
        try:
            state = AgentState(
                session_id="rag-fallback",
                user_id=1,
                user_message="一个知识库没有答案的问题",
            )
            next_node = await graph._node_rag_retrieval(state)
            return state, next_node
        finally:
            graph_module.get_knowledge_base = original

    state, next_node = asyncio.run(scenario())
    assert next_node == "response_generation"
    assert state.collected_info["rag_grounded"] is False
    assert state.collected_info["rag_fallback_reason"] == "low_score"
    assert "没有在知识库中找到" in state.reply
