from unittest.mock import AsyncMock
import pytest
from app.agent.state import AgentNode
from app.agent.flow_contracts import FlowFlag
from app.agent.graph import AgentGraph
from app.models.models import Session
from app.services.chat_service import ChatService
from tests.test_ticket_idempotency import api_context


def old_artifacts():
    return {
        "kb_results": [{"id": "legacy", "title": "旧资料", "content": "synthetic-stale-evidence"}],
        "kb_search_meta": {"degraded": False}, "rag_grounded": True,
        "answer_from_cache": True, "answer_validated": True, "rag_refused": False,
        "validation": {"passed": True}, "llm_judge": {"grounded": True},
        "llm_governance": {"model": "stale-model", "structured_output": {"reply": "stale-draft"}},
        "user_memory": {"recalled": [{"content": "stale-memory"}]},
        "smalltalk": False, "handoff_attempted": True, "reply_source": "stale-source",
        "query_order_result": {"order_id": "stale-order"}, "confirm_refund": True,
        "user_id": 202, "session_id": "other-session",
    }


async def test_sql_hydration_cannot_reintroduce_prior_evidence_review_or_model_metadata(tmp_path, monkeypatch):
    async with api_context(tmp_path / "turn-reset.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        monkeypatch.setattr("app.services.chat_service.session_manager.get_state", AsyncMock(return_value=None))
        async with factory() as db:
            (await db.get(Session, "session-101")).context_data = {
                "collected_info": {**old_artifacts(), "order_id": "ORD950012", "intent_history": ["technical"]}}
            await db.commit()
        state = await ChatService().hydrate_state("session-101", 101, "你好", recover_from_db=True)
        assert not any(key in state.collected_info for key in old_artifacts())
        assert state.collected_info["order_id"] == "ORD950012"
        assert state.current_evidence() == [] and not state.generation_metadata
        assert state.flow_value(FlowFlag.VALIDATED) is None
        assert state.flow_value(FlowFlag.HANDOFF_ATTEMPTED) is None


async def test_new_greeting_after_legacy_sql_snapshot_never_publishes_old_evidence(tmp_path, monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    async with api_context(tmp_path / "greeting.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        monkeypatch.setattr("app.services.chat_service.session_manager.get_state", AsyncMock(return_value=None))
        async with factory() as db:
            (await db.get(Session, "session-101")).context_data = {"collected_info": old_artifacts()}
            await db.commit()
        graph = AgentGraph()
        graph.checkpointer = InMemorySaver()
        graph.langgraph = graph._build_langgraph()
        try:
            state = await ChatService(graph).hydrate_state("session-101", 101, "你好", recover_from_db=True)
            state = await graph.run(state)
            assert state.current_node == AgentNode.END and "您好" in state.reply
            assert "synthetic-stale-evidence" not in state.reply and "参考来源" not in state.reply
            assert not state.current_evidence() and not state.tool_calls
        finally:
            await graph.llm_service.close()


@pytest.mark.parametrize("missing_slots", [[], ["reason"]])
async def test_common_turn_projection_keeps_only_business_context_and_unfinished_parameters(tmp_path, monkeypatch, missing_slots):
    async with api_context(tmp_path / "parameters.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        monkeypatch.setattr("app.services.chat_service.session_manager.get_state", AsyncMock(return_value=None))
        info = {**old_artifacts(), "order_id": "ORD950012", "ticket_id": "synthetic-ticket",
                "tracking_number": "synthetic-tracking", "intent_history": ["refund"],
                "reason": "合成商品破损原因", "refund_type": "refund"}
        # 不含持久预览，只有补参提示。它不能授权提交，但必要参数不应被全部遗忘。
        async with factory() as db:
            (await db.get(Session, "session-101")).context_data = {
                "collected_info": info, "missing_slots": missing_slots, "pending_confirmations": []}
            await db.commit()
        state = await ChatService().hydrate_state("session-101", 101, "合成新消息", recover_from_db=True)
        assert state.collected_info["order_id"] == "ORD950012"
        assert state.collected_info["ticket_id"] == "synthetic-ticket"
        if missing_slots:
            assert state.collected_info["reason"] == "合成商品破损原因"
        else:
            assert "reason" not in state.collected_info and "refund_type" not in state.collected_info
        assert "llm_governance" not in state.collected_info
