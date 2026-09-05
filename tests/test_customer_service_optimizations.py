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
