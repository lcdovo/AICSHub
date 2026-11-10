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


def test_charging_indicator_troubleshooting_is_technical_not_order_query():
    from app.agent.intent import EnhancedIntentRecognizer

    result = EnhancedIntentRecognizer().recognize(
        "耳机充电时没有指示灯怎么排查", llm_primary=False
    )

    assert result.intent == "technical"


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


def test_high_confidence_skips_llm_fallback():
    """⑤ 分层阈值：规则已明显自信（top1 领先 + 高置信）→ 不需要 LLM 兜底"""
    for msg in ("我要退款，商品破损", "订单 ORD123456 现在到哪了？", "转人工", "能退吗"):
        result = recognizer.recognize(msg)
        assert result.needs_llm_fallback is False, msg


def test_low_confidence_keeps_llm_fallback():
    """⑤ 分层阈值：低置信或意图模糊 → 保留 LLM 兜底机会"""
    result = recognizer.recognize("退")
    assert result.needs_llm_fallback is True
    result = recognizer.recognize("坑爹啊")
    assert result.needs_llm_fallback is True


def test_general_never_triggers_llm_fallback():
    """⑤ general（无领域词）不走 LLM 兜底，由澄清/模板/检索接管"""
    result = recognizer.recognize("嗯")
    assert result.intent == "general"
    assert result.needs_llm_fallback is False


def test_continuation_relaxed_threshold():
    """③ 放宽延续：len<20 且 conf<0.4 的追问直接沿用上一意图"""
    for msg in ("大概什么时候能到", "那现在呢"):
        result = recognizer.recognize(msg, context=[{"role": "user", "content": "帮我查个订单"}], history_intent="query_order")
        assert result.intent == "query_order", msg
        assert result.needs_llm_fallback is False, msg
