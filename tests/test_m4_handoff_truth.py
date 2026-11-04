from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
import pytest
from app.agent.state import AgentState, AgentNode
from app.agent.nodes.response_node import ResponseNode, ResponseServices
from tests.test_chat_turns import Cache, Validator


@pytest.mark.parametrize("status", ["pending", "assigned", "resolved", "cancelled"])
async def test_response_uses_owned_handoff_status_without_calling_model(status):
    from app.services.ticket_creation import handoff_message
    model = SimpleNamespace(chat=AsyncMock(side_effect=AssertionError("receipt needs no model")))
    node = ResponseNode(ResponseServices(lambda: model, lambda: Validator(), lambda: None,
                                         lambda: Cache(), lambda: None, lambda: None))
    data = {"request_id": "synthetic-request", "user_id": 101, "session_id": "s", "status": status}
    state = AgentState(session_id="s", user_id=101, user_message="转人工", need_human=True)
    state.add_tool_call("escalate_to_human", {}, {"success": True, "data": data}, True)
    assert await node(state) == AgentNode.END
    assert state.reply == handoff_message(data)


@pytest.mark.parametrize("changes,success", [
    ({"user_id": 202}, True), ({"user_id": True}, True),
    ({"session_id": "another-session"}, True), ({"request_id": " "}, True),
    ({"status": "connected"}, True), ({}, False),
])
async def test_invalid_or_failed_handoff_receipt_does_not_claim_registration(changes, success):
    model = SimpleNamespace(chat=AsyncMock(side_effect=AssertionError("unknown outcome needs no model")))
    node = ResponseNode(ResponseServices(lambda: model, lambda: Validator(), lambda: None,
                                         lambda: Cache(), lambda: None, lambda: None))
    data = {"request_id": "synthetic-request", "user_id": 101, "session_id": "s",
            "status": "pending", **changes}
    state = AgentState(session_id="s", user_id=101, user_message="转人工", need_human=True)
    state.add_tool_call("escalate_to_human", {}, {"success": success, "data": data}, success)
    assert await node(state) == AgentNode.END
    assert "无法确认" in state.reply and "synthetic-request" not in state.reply


async def test_recommendation_alone_says_request_has_not_been_created():
    model = SimpleNamespace(chat=AsyncMock(side_effect=AssertionError("recommendation needs no model")))
    node = ResponseNode(ResponseServices(lambda: model, lambda: Validator(), lambda: None,
                                         lambda: Cache(), lambda: None, lambda: None))
    state = AgentState(session_id="s", user_id=101, user_message="合成问题", need_human=True)
    assert await node(state) == AgentNode.END
    assert "尚未创建" in state.reply and "正在为您转接" not in state.reply


async def test_handoff_recommendation_does_not_override_actual_ticket_receipt_with_transfer_claim():
    model = SimpleNamespace(chat=AsyncMock(side_effect=AssertionError("ticket receipt needs no model")))
    node = ResponseNode(ResponseServices(lambda: model, lambda: Validator(), lambda: None,
                                         lambda: Cache(), lambda: None, lambda: None))
    state = AgentState(session_id="s", user_id=101, user_message="我要投诉服务态度",
                       detected_intent="complaint", need_human=True, human_reason="历史意图升级建议")
    state.add_tool_call("create_ticket", {}, {"success": True, "data": {
        "ticket_id": "synthetic-ticket", "message": "投诉工单 synthetic-ticket 已创建，请查看后续处理状态。"}}, True)
    assert await node(state) == AgentNode.END
    assert "synthetic-ticket" in state.reply
    assert "正在为您转接" not in state.reply
    assert not any(t.tool_name == "escalate_to_human" for t in state.tool_calls)


@pytest.mark.parametrize("need_human", [False, True])
async def test_recommendation_without_persisted_handoff_is_not_counted_as_created_request(tmp_path, monkeypatch, need_human):
    from app.models.models import Session
    from app.services.chat_service import ChatService
    from tests.test_ticket_idempotency import api_context
    tracer = Mock()
    monkeypatch.setattr("app.services.chat_service.get_tracer", lambda: tracer)
    async with api_context(tmp_path / "handoff-metric.db") as (_, factory):
        state = AgentState(session_id="session-101", user_id=101, user_message="合成问题",
                           reply="建议人工进一步协助，尚未创建请求。", need_human=need_human)
        async with factory() as db:
            await ChatService().persist_result(db, await db.get(Session, "session-101"), state,
                                               state.user_message, "synthetic-trace", commit=False)
            await db.commit()
        tracer.record_handoff.assert_not_called()


@pytest.mark.parametrize("record_scope", ["missing", "owned", "other-user", "other-session"])
async def test_handoff_outcome_metric_requires_actual_owned_sql_request_even_without_recommendation_flag(tmp_path, monkeypatch, record_scope):
    from app.models.models import Session, User
    from app.services.chat_service import ChatService
    from app.services.ticket_creation import create_request, handoff_receipt, handoff_message
    from tests.test_ticket_idempotency import api_context
    tracer = Mock()
    monkeypatch.setattr("app.services.chat_service.get_tracer", lambda: tracer)
    async with api_context(tmp_path / "actual-handoff.db") as (_, factory):
        async with factory() as db:
            if record_scope != "missing":
                owner = 202 if record_scope == "other-user" else 101
                session_id = f"session-{owner}"
                if record_scope == "other-session":
                    session_id = "session-other-101"
                    db.add(Session(id=session_id, user_id=101))
                    await db.commit()
                row, _ = await create_request(db, await db.get(User, owner), "request_handoff", {
                    "user_id": owner, "session_id": session_id, "reason": "合成请求", "priority": "normal", "context": ""}, "synthetic-handoff")
                data = handoff_receipt(row)
                # 伪造同scope工具回执不能把另一用户/会话的真实SQL请求计入当前轮。
                data.update(user_id=101, session_id="session-101")
            else:
                data = {"request_id": "missing", "user_id": 101, "session_id": "session-101", "status": "pending"}
        state = AgentState(session_id="session-101", user_id=101, user_message="合成问题",
                           reply=handoff_message(data), need_human=False)
        state.add_tool_call("escalate_to_human", {}, {"success": True, "data": data}, True)
        async with factory() as db:
            await ChatService().persist_result(db, await db.get(Session, "session-101"), state,
                                               state.user_message, "synthetic-trace", commit=False)
            await db.commit()
        assert tracer.record_handoff.call_count == int(record_scope == "owned")
