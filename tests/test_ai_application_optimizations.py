import asyncio

from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.services.chat_service import ChatService


def test_low_confidence_intent_uses_structured_fallback(monkeypatch):
    async def scenario():
        graph = AgentGraph()

        async def classify(_message, context=None):
            return {"intent": "technical", "confidence": 0.88, "need_clarification": False}

        graph.llm_service.classify_intent = classify
        state = AgentState(session_id="intent-fallback", user_id=1, user_message="这个怎么弄")
        await graph._node_intent_recognition(state)
        return state

    state = asyncio.run(scenario())
    assert state.detected_intent == "technical"
    assert state.intent_confidence == 0.88
    assert state.collected_info["intent_source"] == "llm_fallback"


def test_chat_service_hydrates_saved_context(monkeypatch):
    async def scenario():
        service = ChatService(AgentGraph())
        previous = AgentState(
            session_id="shared-service",
            user_id=1,
            user_message="first",
            detected_intent="query_order",
            collected_info={"order_id": "ORD20260801"},
            messages=[{"role": "assistant", "content": "previous"}],
        )
        monkeypatch.setattr(
            "app.services.chat_service.session_manager.get_state",
            lambda _session_id: asyncio.sleep(0, result=previous),
        )
        state = await service.hydrate_state("shared-service", 1, "物流怎么样")
        return state

    state = asyncio.run(scenario())
    assert state.detected_intent == "query_order"
    assert state.collected_info["order_id"] == "ORD20260801"
    assert state.messages[-1]["content"] == "物流怎么样"


def test_refund_cancellation_clears_pending_action():
    async def scenario():
        graph = AgentGraph()
        state = AgentState(
            session_id="refund-cancel",
            user_id=1,
            user_message="取消",
            detected_intent="refund",
            pending_confirmations=["apply_refund"],
            collected_info={
                "refund_preview": {
                    "order_id": "ORD20260801",
                    "reason": "商品质量问题",
                    "type": "refund",
                }
            },
        )
        return await graph.run(state)

    state = asyncio.run(scenario())
    assert state.pending_confirmations == []
    assert "refund_preview" not in state.collected_info
    assert not any(call.tool_name == "apply_refund" for call in state.tool_calls)
    assert "已取消" in state.reply
