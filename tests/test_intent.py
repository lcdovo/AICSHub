"""意图识别单元测试：关键词加权、否定词拦截、上下文推断与澄清触发。"""
from app.agent.intent import EnhancedIntentRecognizer

recognizer = EnhancedIntentRecognizer()


def test_order_id_boosts_query_order():
    result = recognizer.recognize("帮我查一下订单 ORD123456 的物流")
    assert result.intent == "query_order"


def test_refund_action_overrides_order_id():
    # 明确退款动作时业务动作优先于查单（即使消息含订单号）
    result = recognizer.recognize("ORD123456 我要退款")
    assert result.intent == "refund"


def test_negation_blocks_refund_keyword():
    result = recognizer.recognize("不是要退款，只是问问")
    assert result.intent == "general"


def test_low_confidence_triggers_clarification():
    result = recognizer.recognize("退")
    assert result.needs_clarification


def test_context_inference_from_tool_call():
    context = [{"role": "tool", "tool_name": "query_order", "content": "{}"}]
    result = recognizer.recognize("好的", context=context)
    assert result.intent == "query_order"


def test_continuation_keeps_history_intent():
    result = recognizer.recognize("然后呢", context=[], history_intent="query_order")
    assert result.intent == "query_order"


def test_tracking_number_boosts_query_order():
    result = recognizer.recognize("SF12345678 走到哪了")
    assert result.intent == "query_order"


def test_generic_request_triggers_clarification():
    result = recognizer.recognize("我有个问题")
    assert result.needs_clarification


def test_short_general_message_clarifies():
    result = recognizer.recognize("嗯")
    assert result.needs_clarification is True


def test_normal_order_query_not_clarified():
    result = recognizer.recognize("帮我查一下订单 ORD123456")
    assert result.needs_clarification is False


def test_refund_with_order_and_action_keeps_refund():
    result = recognizer.recognize("ORD123456 我要退款，商品坏了")
    assert result.intent == "refund"


def test_full_policy_context_not_negated():
    """否定词"不要"不能误伤"要不要"场景里的正向词"""
    result = recognizer.recognize("要不要退款优惠？")
    assert result.intent in ("refund", "promotion")
