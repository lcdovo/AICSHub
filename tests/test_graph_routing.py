"""
Agent 图级路由单测：意图→节点路由、槽位判定、退款分流、意图升级检测。

覆盖 graph.py 中与 LLM/存储无依赖的纯路由逻辑（用 __new__ 绕过 __init__）：
1. _route_by_intent 各意图对应目标节点
2. _missing_slots_for_intent 订单/退款缺失槽位判定
3. _is_refund_policy_inquiry 退款政策咨询 vs 实际申请分流
4. _detect_intent_escalation 升级链检测
5. _refund_confirmed / _refund_cancelled 确认/取消识别
6. general 三级链路：_is_smalltalk 判定、路由分流、寒暄模板早退
"""
import asyncio

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, AgentNode, IntentType


def _make_graph():
    return object.__new__(AgentGraph)


def test_route_by_intent_mapping():
    graph = _make_graph()
    assert graph._route_by_intent(IntentType.QUERY_ORDER) == AgentNode.TOOL_EXECUTION
    assert graph._route_by_intent(IntentType.REFUND) == AgentNode.TOOL_EXECUTION
    assert graph._route_by_intent(IntentType.COMPLAINT) == AgentNode.TOOL_EXECUTION
    assert graph._route_by_intent(IntentType.TECHNICAL) == AgentNode.QUERY_REWRITE
    assert graph._route_by_intent(IntentType.PROMOTION) == AgentNode.QUERY_REWRITE
    assert graph._route_by_intent(IntentType.HUMAN) == AgentNode.HUMAN_HANDOFF
    assert graph._route_by_intent(IntentType.GENERAL) == AgentNode.RESPONSE_GENERATION
    # unknown 兜底到响应生成
    assert graph._route_by_intent(IntentType.UNKNOWN) == AgentNode.RESPONSE_GENERATION


def _state_with_intent(intent: str, **info) -> AgentState:
    state = AgentState(session_id="s1", user_id=1, user_message="x")
    state.detected_intent = intent
    state.collected_info.update(info)
    return state


def test_missing_slots_order_requires_order_id():
    graph = _make_graph()
    state = _state_with_intent(IntentType.QUERY_ORDER)
    assert graph._missing_slots_for_intent(state) == ["order_id"]
    state.collected_info["order_id"] = "ORD123456"
    assert graph._missing_slots_for_intent(state) == []


def test_missing_slots_refund_requires_order_and_reason():
    graph = _make_graph()
    state = _state_with_intent(IntentType.REFUND)
    assert graph._missing_slots_for_intent(state) == ["order_id", "reason"]

    state = _state_with_intent(IntentType.REFUND, order_id="ORD1", reason="商品外包装破损很严重")
    assert graph._missing_slots_for_intent(state) == []


def test_missing_slots_refund_reason_too_short():
    graph = _make_graph()
    state = _state_with_intent(IntentType.REFUND, order_id="ORD1", reason="不好")
    assert graph._missing_slots_for_intent(state) == ["reason"]


def test_refund_policy_inquiry_goes_to_rag():
    graph = _make_graph()
    # 政策咨询：有政策词、无动作词、无订单号
    assert graph._is_refund_policy_inquiry(
        "退款流程是什么", {}
    ) is True
    # "能退吗" 类提问无订单号 → 政策咨询
    assert graph._is_refund_policy_inquiry("这件商品能退吗", {}) is True


def test_refund_action_with_order_goes_to_tool():
    graph = _make_graph()
    assert graph._is_refund_policy_inquiry(
        "我要申请退款", {"order_id": "ORD123456"}
    ) is False


def test_refund_policy_with_action_word_not_inquiry():
    graph = _make_graph()
    # 有政策词但同时含动作词 → 实际申请
    assert graph._is_refund_policy_inquiry(
        "我要退钱，退货流程是？", {}
    ) is False


def test_escalation_order_refund_complaint():
    graph = _make_graph()
    history = [IntentType.QUERY_ORDER, IntentType.REFUND]
    chain = graph._detect_intent_escalation(history, IntentType.COMPLAINT)
    assert chain == ["query_order", "refund", "complaint"]


def test_escalation_query_order_human():
    graph = _make_graph()
    chain = graph._detect_intent_escalation(
        [IntentType.QUERY_ORDER], IntentType.HUMAN
    )
    assert chain == ["query_order", "human"]


def test_no_escalation_on_benign_sequence():
    graph = _make_graph()
    chain = graph._detect_intent_escalation(
        [IntentType.GENERAL, IntentType.QUERY_ORDER], IntentType.REFUND
    )
    assert chain is None


def test_refund_confirmed_short_words():
    graph = _make_graph()
    state = _state_with_intent(IntentType.REFUND)
    state.user_message = "确认"
    assert graph._refund_confirmed(state) is True


def test_refund_not_confirmed_thanks():
    graph = _make_graph()
    state = _state_with_intent(IntentType.REFUND)
    state.user_message = "好的，谢谢"
    assert graph._refund_confirmed(state) is False


def test_refund_cancelled_words():
    graph = _make_graph()
    state = _state_with_intent(IntentType.REFUND)
    state.user_message = "取消"
    assert graph._refund_cancelled(state) is True


def _smalltalk_state(message: str, smalltalk: bool) -> AgentState:
    """构造 general 意图 state，可开关寒暄标记。"""
    state = _state_with_intent(IntentType.GENERAL, smalltalk=smalltalk)
    state.user_message = message
    return state


def test_is_smalltalk_greeting_thanks_farewell():
    graph = _make_graph()
    # 问候
    assert graph._is_smalltalk("你好") is True
    assert graph._is_smalltalk("在吗") is True
    assert graph._is_smalltalk("早上好") is True
    # 感谢
    assert graph._is_smalltalk("谢谢") is True
    assert graph._is_smalltalk("多谢您帮忙") is True
    # 告别
    assert graph._is_smalltalk("再见") is True
    assert graph._is_smalltalk("bye") is True


def test_is_smalltalk_rejects_non_smalltalk():
    graph = _make_graph()
    # 真实泛问：需要走 RAG 检索兜底
    assert graph._is_smalltalk("你们的西瓜甜不甜") is False
    # 短应答保持澄清行为，不视为寒暄
    assert graph._is_smalltalk("好的") is False
    assert graph._is_smalltalk("嗯") is False
    assert graph._is_smalltalk("") is False
    assert graph._is_smalltalk(None) is False


def test_route_decision_general_smalltalk_goes_to_template():
    graph = _make_graph()
    state = _smalltalk_state("你好", smalltalk=True)
    assert asyncio.run(graph._node_route_decision(state)) == AgentNode.RESPONSE_GENERATION
    assert state.route_reason == "general:smalltalk->template"


def test_route_decision_general_rag_fallback():
    graph = _make_graph()
    state = _smalltalk_state("你们的西瓜甜不甜", smalltalk=False)
    assert asyncio.run(graph._node_route_decision(state)) == AgentNode.QUERY_REWRITE
    assert state.route_reason == "general:rag_fallback"


def test_smalltalk_reply_uses_template_without_llm(monkeypatch):
    graph = _make_graph()
    state = _smalltalk_state("你好", smalltalk=True)

    async def _should_not_be_called(state):
        raise AssertionError("寒暄回复不应调用 LLM 生成")

    # 若小语对话误入 LLM 分支，测试将失败
    monkeypatch.setattr(AgentGraph, "_generate_response_with_llm", _should_not_be_called)
    result = asyncio.run(graph._node_response_generation(state))
    assert result == AgentNode.END
    assert state.reply == "您好，请问有什么可以帮您？"


def test_default_reply_general_farewell_template():
    graph = _make_graph()
    assert graph._get_default_reply(IntentType.GENERAL, "再见") == "再见，祝您生活愉快，有需要随时找我。"


def test_slot_filling_order_guidance_not_bare_request():
    """缺订单号时给出查找指引话术，而非单句索要参数。"""
    graph = _make_graph()
    state = _state_with_intent(IntentType.QUERY_ORDER)
    state.missing_slots = ["order_id"]
    assert asyncio.run(graph._node_slot_filling(state)) == AgentNode.RESPONSE_GENERATION
    assert "我的订单" in state.reply
    assert "ORD" in state.reply


def test_slot_filling_refund_keeps_argument_labels():
    """退款槽位提示保留补齐参数格式。"""
    graph = _make_graph()
    state = _state_with_intent(IntentType.REFUND)
    state.missing_slots = ["order_id", "reason"]
    assert asyncio.run(graph._node_slot_filling(state)) == AgentNode.RESPONSE_GENERATION
    assert "请补充" in state.reply