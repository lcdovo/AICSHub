"""固定业务P0反例。合成领域回执，不代表完整自然语言质量集。"""
import pytest
from app.agent.validation import ResponseValidator


def order(status="已发货", amount="299.00", order_id="ORD950099"):
    return {"success": True, "data": {"order_id": order_id, "status": status,
            "status_code": "shipped" if status == "已发货" else "cancelled", "total_amount": amount, "currency": "CNY"}}


@pytest.mark.parametrize("reply", [
    "订单ORD950099还没发货。", "订单ORD950099状态：已取消。",
    "订单ORD950099已发货，金额399元。", "订单ORD950099已发货，金额299美元。",
    "订单ORD950099已发货，金额299元，另赔偿1000元。",
    "订单ORD950099已发货，享受终身换新。",
])
def test_critical_contradiction_is_blocked_independent_of_average_score(reply):
    result = ResponseValidator().validate(reply, "查订单ORD950099", "query_order", [order()])
    assert not result.passed and any(i["severity"] == "critical" for i in result.issues)


@pytest.mark.parametrize("tools", [[], [{"success": False, "data": {}}],
    [{"success": True, "data": {"message": "退款成功"}}],
    [{"success": True, "data": {"refund_id": "synthetic-refund", "status": "pending_review", "is_simulated": True}}],
])
def test_refund_payment_success_requires_real_payment_receipt(tools):
    result = ResponseValidator().validate("退款成功，299元已到账。", "退款到账了吗", "refund", tools)
    assert not result.passed and result.fact_score < .6


def test_unrelated_last_success_cannot_hide_first_order_contradiction():
    unrelated = {"success": True, "data": {"ticket_id": "synthetic-ticket", "status": "pending"}}
    result = ResponseValidator().validate("订单ORD950099还没发货。", "查ORD950099", "query_order", [order(), unrelated])
    assert not result.passed


def test_conflicting_same_order_receipts_require_clarification():
    result = ResponseValidator().validate("订单ORD950099已发货，总金额299元。", "查ORD950099", "query_order",
                                          [order(), order(status="已取消", amount="399.00")])
    assert not result.passed


@pytest.mark.parametrize("reply", ["已发货。", "订单ORD950099已发货。", "订单ORD950099已发货，总金额299元。"])
def test_short_correct_answer_is_not_rejected_for_not_repeating_all_fields(reply):
    assert ResponseValidator().validate(reply, "订单ORD950099状态？", "query_order", [order()]).passed


@pytest.mark.parametrize("reply", ["警惕诈骗，不要转账。", "请勿提供密码或验证码。", "不要参与赌博，请保护个人信息。"])
def test_legitimate_safety_advice_is_not_rejected_by_sensitive_word(reply):
    assert ResponseValidator().validate(reply, "怎么防诈骗？", "general", []).passed


def test_per_order_scope_does_not_mix_other_orders_amounts():
    assert ResponseValidator().validate("订单ORD950099已发货，金额299元。", "查ORD950099", "query_order",
                                        [order(), order(amount="999.00", order_id="ORD950100")]).passed


@pytest.mark.parametrize("reply", ["退款申请已提交。", "已登记您的退款申请。"])
def test_application_claim_without_application_receipt_is_not_success(reply):
    assert not ResponseValidator().validate(reply, "申请退款", "refund", []).passed


def test_partial_success_receipt_is_not_an_application_fact():
    tools = [{"success": True, "data": {"refund_id": "partial", "amount": 99}}]
    assert not ResponseValidator().validate("退款申请已提交。", "申请退款", "refund", tools).passed


async def test_workflow_flag_cannot_bypass_fixed_business_facts_or_publish_stream():
    import asyncio
    from app.agent.state import AgentState, AgentNode
    from app.agent.nodes.response_node import ResponseNode, ResponseServices
    from app.agent.stream_context import stream_event_queue
    from tests.test_chat_turns import Cache
    state = AgentState(session_id="s", user_id=101, user_message="合成问题", reply="退款成功，299元已到账。",
                       needs_clarification=True)
    node = ResponseNode(ResponseServices(lambda: None, ResponseValidator, lambda: None,
                                         Cache, lambda: None, lambda: None))
    queue = asyncio.Queue()
    token = stream_event_queue.set(queue)
    try:
        assert await node(state) == AgentNode.END
    finally:
        stream_event_queue.reset(token)
    assert "退款成功" not in state.reply
    assert all("退款成功" not in queue.get_nowait()[1] for _ in range(queue.qsize()))
    assert not state.tool_calls and not state.review_result.passed
