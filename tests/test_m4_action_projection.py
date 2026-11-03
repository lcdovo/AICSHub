"""真实隔离SQL，缓存和checkpoint故意陈旧；SQL动作是恢复权威。"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select, func
from app.agent.state import AgentState, AgentNode
from app.agent.graph import AgentGraph
from app.models.models import Session, Order, RefundAction, RefundApplication
from app.services.chat_service import ChatService
from app.services.refund_actions import prepare
from tests.test_ticket_idempotency import api_context


def projection(action_id):
    return {"pending_confirmations": ["apply_refund"], "missing_slots": [],
            "collected_info": {"order_id": "ORD950005", "reason": "旧缓存伪造原因",
                               "refund_type": "refund", "confirm_refund": True,
                               "refund_preview": {"action_id": action_id, "order_id": "ORD950005",
                                                  "reason": "旧缓存伪造原因", "type": "refund"},
                               "refund_action": {"action_id": action_id}}}


@pytest.mark.parametrize("status", ["missing", "cancelled", "expired"])
async def test_default_hydration_does_not_resurrect_dead_action_projection(tmp_path, monkeypatch, status):
    async with api_context(tmp_path / "projection.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        action_id = "missing-action"
        if status != "missing":
            async with factory() as db:
                db.add(Order(order_id="ORD950005", user_id=101, status="已发货", status_code="shipped",
                             total_amount="0.10", items=[]))
                await db.commit()
            action = await prepare(factory, 101, dict(user_id=101, session_id="session-101",
                                                    order_id="ORD950005", type="refund", reason="合成商品破损原因"))
            action_id = action["action_id"]
            async with factory() as db:
                row = await db.get(RefundAction, action_id)
                if status == "expired":
                    row.expires_at = datetime.utcnow() - timedelta(seconds=1)
                else:
                    row.status = "cancelled"
                await db.commit()
        async with factory() as db:
            (await db.get(Session, "session-101")).context_data = projection(action_id)
            await db.commit()
        saved = AgentState(session_id="session-101", user_id=101, user_message="旧消息",
                           pending_confirmations=["apply_refund"], collected_info=projection(action_id)["collected_info"])
        monkeypatch.setattr("app.services.chat_service.session_manager.get_state", AsyncMock(return_value=saved))
        state = await ChatService().hydrate_state("session-101", 101, "确认", recover_from_db=True)
        assert "apply_refund" not in state.pending_confirmations
        assert "refund_preview" not in state.collected_info
        assert "refund_action" not in state.collected_info
        assert "confirm_refund" not in state.collected_info
        assert state.conversation_context.pending_action_id is None


async def test_live_sql_action_overrides_forged_cache_confirmation_and_parameters(tmp_path, monkeypatch):
    async with api_context(tmp_path / "current.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        async with factory() as db:
            db.add(Order(order_id="ORD950005", user_id=101, status="已发货", status_code="shipped",
                         total_amount="0.10", items=[]))
            await db.commit()
        action = await prepare(factory, 101, dict(user_id=101, session_id="session-101",
                                                order_id="ORD950005", type="refund", reason="合成商品破损原因"))
        async with factory() as db:
            (await db.get(Session, "session-101")).context_data = projection("forged-action")
            await db.commit()
        monkeypatch.setattr("app.services.chat_service.session_manager.get_state", AsyncMock(return_value=None))
        state = await ChatService().hydrate_state("session-101", 101, "咨询一下", recover_from_db=True)
        assert state.collected_info["refund_preview"]["action_id"] == action["action_id"]
        assert state.collected_info["reason"] == "合成商品破损原因"
        assert state.collected_info.get("confirm_refund") is not True
        assert state.conversation_context.pending_action_id == action["action_id"]


async def test_old_checkpoint_cannot_restore_cancelled_sql_action_in_new_turn(tmp_path, monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    async with api_context(tmp_path / "checkpoint.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        monkeypatch.setattr("app.services.chat_service.session_manager.get_state", AsyncMock(return_value=None))
        graph = AgentGraph()
        graph.checkpointer = InMemorySaver()
        graph.langgraph = graph._build_langgraph()
        monkeypatch.setattr(graph.llm_service, "is_available", lambda: False)
        monkeypatch.setattr(graph.llm_service, "classify_intent", AsyncMock(return_value=None))
        async with factory() as db:
            db.add(Order(order_id="ORD950005", user_id=101, status="已发货", status_code="shipped",
                         total_amount="0.10", items=[]))
            await db.commit()
        action = await prepare(factory, 101, dict(user_id=101, session_id="session-101",
                                                order_id="ORD950005", type="refund", reason="合成商品破损原因"))
        async with factory() as db:
            (await db.get(RefundAction, action["action_id"])).status = "cancelled"
            (await db.get(Session, "session-101")).context_data = projection(action["action_id"])
            await db.commit()
        old = AgentState(session_id="session-101", user_id=101, user_message="确认", actor_id=101,
                         pending_confirmations=["apply_refund"], collected_info=projection(action["action_id"])["collected_info"])
        await graph.langgraph.aupdate_state(
            {"configurable": {"thread_id": "session-101"}},
            {"state": old, "next_node": AgentNode.TOOL_EXECUTION}, as_node=AgentNode.START)
        try:
            state = await ChatService(graph).hydrate_state("session-101", 101, "确认", recover_from_db=True)
            state = await graph.run(state)
            assert not state.pending_confirmations
            assert not any(tc.tool_name == "apply_refund" for tc in state.tool_calls)
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(RefundApplication)) == 0
                assert (await db.get(RefundAction, action["action_id"])).status == "cancelled"
        finally:
            await graph.llm_service.close()
