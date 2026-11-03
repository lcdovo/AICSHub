from unittest.mock import AsyncMock
import pytest
from app.agent.state import AgentState, AgentNode
from app.agent.nodes.tool_node import (
    ConfirmationNode, ConfirmationDependencies, ToolExecutionNode, ToolExecutionDependencies,
    extract_info, select_tools, is_refund_confirmed,
)
from app.agent.tools import ToolExecutionResult


def state():
    return AgentState(session_id="s", user_id=101, actor_id=101, source_message_id=7,
                      user_message="订单ORD950015破损，请申请退款", detected_intent="refund",
                      collected_info={"order_id": "ORD950015", "reason": "合成商品严重破损",
                                      "refund_type": "refund", "refund_preview": {
                                          "order_id": "ORD950015", "reason": "合成商品严重破损", "type": "refund",
                                          "action_id": "synthetic-action", "action_version": 1,
                                          "user_id": 101, "session_id": "s", "idempotency_key": "synthetic-key"}})


def test_old_boolean_flag_does_not_authorize_a_nonconfirmation_message():
    value = state()
    value.collected_info["confirm_refund"] = True
    assert not is_refund_confirmed(value)


@pytest.mark.parametrize("field", ["message", "source", "user", "actor", "session", "order", "reason", "version", "action", "type", "key"])
async def test_pause_confirmation_is_invalid_after_context_or_preview_changes(field):
    value = state()
    confirm = ConfirmationNode(ConfirmationDependencies(is_refund_confirmed, lambda _: True))
    assert await confirm(value) == AgentNode.TOOL_EXECUTION
    assert is_refund_confirmed(value)
    if field == "message": value.user_message = "给另一个订单退款"
    elif field == "source": value.source_message_id = 8
    elif field == "user": value.user_id = 202
    elif field == "actor": value.actor_id = 999
    elif field == "session": value.session_id = "other"
    elif field == "order": value.collected_info["refund_preview"]["order_id"] = "ORD950016"
    elif field == "reason": value.collected_info["refund_preview"]["reason"] = "另一原因"
    elif field == "version": value.collected_info["refund_preview"]["action_version"] = 2
    elif field == "action": value.collected_info["refund_preview"]["action_id"] = "another-action"
    elif field == "type": value.collected_info["refund_preview"]["type"] = "exchange"
    else: value.collected_info["refund_preview"]["idempotency_key"] = "another-key"
    assert not is_refund_confirmed(value)


async def test_trusted_pause_confirmation_survives_tool_step_for_same_context_but_not_next_turn():
    value = state()
    value.source_message_id = None  # 仅验证节点契约；持久提交仍需要SQL当前确认，真实领域不在此替身中执行。
    confirm = ConfirmationNode(ConfirmationDependencies(is_refund_confirmed, lambda _: True))
    assert await confirm(value) == AgentNode.TOOL_EXECUTION
    invoke = AsyncMock(return_value=ToolExecutionResult(success=True, data={"refund_id": "synthetic-refund"}))
    tool = ToolExecutionNode(ToolExecutionDependencies(extract_info, select_tools, is_refund_confirmed, invoke, None))
    assert await tool(value) == AgentNode.RESULT_VERIFICATION
    invoke.assert_awaited_once()
    fresh = value.for_next_turn(101, "重新申请退款")
    assert fresh.refund_confirmation is None and not is_refund_confirmed(fresh)


def test_message_confirmation_word_set_is_unchanged():
    from app.agent.refund_authorization import CONFIRMATIONS
    value = state()
    for message in CONFIRMATIONS:
        value.user_message = message
        assert is_refund_confirmed(value)
    for message in ["好的", "谢谢", "确认一下退款政策", "上次我说确认"]:
        value.user_message = message
        assert not is_refund_confirmed(value)


async def test_typed_confirmation_observation_cannot_replace_actual_sql_confirmation(tmp_path, monkeypatch):
    from sqlalchemy import select, func
    from app.models.models import Message, MessageRole, Order, RefundApplication
    from app.services.refund_actions import prepare
    from app.agent.refund_authorization import authorize_refund
    from app.agent.tools import ApplyRefundTool
    from tests.test_ticket_idempotency import api_context

    async with api_context(tmp_path / "confirmation.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        async with factory() as db:
            db.add(Order(order_id="ORD950015", user_id=101, total_amount="0.10",
                         status="已发货", status_code="shipped", items=[]))
            source = Message(session_id="session-101", role=MessageRole.USER, content="商品破损，请先展示退款预览")
            db.add(source)
            await db.commit()
            source_id = source.id
        action = await prepare(factory, 101, dict(user_id=101, session_id="session-101",
                                                order_id="ORD950015", type="refund", reason="合成商品严重破损"))
        payload = dict(user_id=101, session_id="session-101", order_id="ORD950015", type="refund",
                       reason="合成商品严重破损", action_id=action["action_id"], action_version=action["version"],
                       idempotency_key="refund-action:" + action["action_id"])
        value = AgentState(session_id="session-101", user_id=101, actor_id=101, source_message_id=source_id,
                           user_message=source.content, collected_info={"refund_preview": payload})
        value.record_refund_confirmation("interrupt")
        assert is_refund_confirmed(value)  # 编排观察已存在，仍不能代替SQL消息事实。
        result = await ApplyRefundTool().execute(**payload, authorization=authorize_refund(payload, source_id, 101))
        assert not result["success"] and result["error_type"] == "permission"
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(RefundApplication)) == 0
