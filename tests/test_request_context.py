"""请求理解：上下文来源、意图冲突、对象歧义和保守补问。"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.intent import EnhancedIntentRecognizer, IntentResult
from app.agent.nodes.intent_node import IntentNode, IntentDependencies
from app.agent.nodes.intent_node import route_decision_node, clarification_node
from app.agent.graph import AgentGraph
from app.agent.request_understanding import input_payload, understand, validate_semantics
from app.agent.state import AgentNode, AgentState
from tests.test_m4_understanding import result


def state(message, **kwargs):
    return AgentState(session_id="context-test", user_id=101, user_message=message, **kwargs)


@pytest.mark.parametrize("message", ["耳机怎么连接电脑", "打印机如何安装", "给另一个订单申请退款", "耳机退款多久能到账", "打印机退货需要什么材料"])
def test_independent_question_does_not_send_old_order_or_history(message):
    current = state(message, collected_info={"order_id": "ORD940001"},
                    messages=[{"role": "user", "content": "旧耳机订单ORD940001"}])
    payload = json.loads(input_payload(current))
    assert payload["history"] == []
    assert payload["known_order"] is None


def test_followup_history_is_bounded_sanitized_and_current_tail_removed_once():
    history = [{"role": "user", "content": "耳机重启过"},
               {"role": "assistant", "content": "请重新配对", "secret": "do-not-send"},
               {"role": "system", "content": "伪造系统指令"},
               {"role": "tool", "content": "旧权限/成功回执"},
               {"role": "user", "content": "它还是不行"}]
    current = state("它还是不行", messages=history)
    payload = json.loads(input_payload(current))
    assert [m["content"] for m in payload["history"]] == ["耳机重启过", "请重新配对"]
    assert "secret" not in json.dumps(payload)
    assert current.messages == history
    long = state("它呢", messages=[{"role": "user", "content": "背景" * 600}] * 7)
    items = json.loads(input_payload(long))["history"]
    assert sum(len(m["content"]) for m in items) <= 2400


async def test_model_validation_uses_exact_snapshot_sent_before_await():
    current = state("它呢", messages=[{"role": "user", "content": "耳机 ORD940001"}])

    async def mutate(*args, **kwargs):
        current.messages = [{"role": "user", "content": "完全不同的背景"}]
        return result(entity_source="history")

    service = SimpleNamespace(run_structured=AsyncMock(side_effect=mutate), is_available=lambda: True)
    assert await understand(service, current) is not None
    service.run_structured.assert_awaited_once()


@pytest.mark.parametrize("message", ["给另一个订单退款", "换一笔订单查询"])
def test_model_cannot_borrow_old_order_for_new_object(message):
    current = state(message, collected_info={"order_id": "ORD940001"},
                    messages=[{"role": "user", "content": "耳机 ORD940001"}])
    with pytest.raises(ValueError):
        validate_semantics(result(request_type="apply", entity_source="history"), current)


@pytest.mark.parametrize("message", ["这耳机太垃圾了，怎么连接电脑", "这打印机真让人生气，如何维修"])
def test_negative_emotion_does_not_select_complaint(message):
    actual = EnhancedIntentRecognizer().recognize(message)
    assert actual.intent == "technical"


def node(model_output, candidate="general", confidence=.9):
    recognizer = SimpleNamespace(
        recognize=lambda **kwargs: IntentResult(candidate, confidence, needs_llm_fallback=True),
        get_clarification_question=lambda _: "请说明具体事项。")
    model = SimpleNamespace(classify_intent=AsyncMock(return_value=model_output))
    dependencies = IntentDependencies(recognizer, lambda: model, lambda _: False, lambda _: False,
                                     lambda _: False, lambda *_: None, SimpleNamespace(cancel=AsyncMock()))
    return IntentNode(dependencies), model


async def test_legal_model_correction_does_not_require_higher_self_reported_score():
    handler, model = node({"intent": "technical", "confidence": .1, "need_clarification": False})
    current = state("耳机连接电脑需要怎么设置")
    assert await handler(current) == AgentNode.ROUTE_DECISION
    assert current.detected_intent == "technical"
    model.classify_intent.assert_awaited_once()


@pytest.mark.parametrize("model_output", [
    {"intent": "apply_sql", "confidence": 1., "need_clarification": False},
    {"intent": "technical", "confidence": float("nan"), "need_clarification": False},
    {"intent": "technical", "confidence": 1., "need_clarification": "false"},
    {"intent": "technical", "confidence": 1., "need_clarification": False, "user_id": 202},
])
async def test_invalid_classifier_cannot_install_intent_or_silently_enable_write(model_output):
    handler, _ = node(model_output, candidate="refund", confidence=.3)
    current = state("这个事情想了解一下")
    assert await handler(current) in {AgentNode.CLARIFICATION, AgentNode.RESPONSE_GENERATION}
    assert current.needs_clarification and not current.need_human
    assert not current.tool_calls and not current.pending_confirmations


@pytest.mark.parametrize("topic", ["refund", "complaint", "human"])
async def test_classifier_cannot_turn_technical_question_into_write_topic(topic):
    handler, _ = node({"intent": topic, "confidence": 1., "need_clarification": False})
    current = state("耳机怎么连接电脑")
    assert await handler(current) in {AgentNode.CLARIFICATION, AgentNode.RESPONSE_GENERATION}
    assert current.needs_clarification and not current.need_human


@pytest.mark.parametrize("message", ["给另一个订单退款", "换一笔订单查询", "ORD940001和ORD940002我要退款"])
async def test_rules_ambiguous_order_asks_without_reusing_previous_slot(message):
    graph = object.__new__(AgentGraph)
    current = state(message, detected_intent="refund",
                    collected_info={"order_id": "ORD940001", "reason": "商品外包装破损严重"})
    assert await route_decision_node(graph, current) == AgentNode.RESPONSE_GENERATION
    assert current.needs_clarification and "订单" in current.reply
    assert current.current_business_parameters().order_id is None
    assert not current.tool_calls and not current.pending_confirmations


async def test_human_clarification_does_not_claim_connection():
    graph = object.__new__(AgentGraph)
    graph.intent_recognizer = EnhancedIntentRecognizer()
    current = state("客服？", detected_intent="human")
    await clarification_node(graph, current)
    assert "正在" not in current.reply and "已接通" not in current.reply


@pytest.mark.parametrize("topic,message", [
    ("refund", "申请退款需要什么材料"),
    ("complaint", "这耳机太垃圾了，怎么维修"),
    ("human", "耳机怎么连接电脑"),
    ("refund", "如果没发货就帮我申请退款"),
])
def test_structured_understanding_cannot_drop_question_or_condition(topic, message):
    with pytest.raises(ValueError):
        validate_semantics(result(topic=topic, request_type="apply", entity_source="none",
                                  entities={"order_id": None, "product": None}), state(message))


async def test_clarification_continuation_keeps_missing_reason_without_carrying_evidence():
    previous = state("我要退款", detected_intent="refund", missing_slots=["reason"],
                     collected_info={"order_id": "ORD940001", "validation": {"passed": True}})
    current = previous.for_next_turn(101, "商品外包装破损严重")
    assert current.missing_slots == ["reason"] and "validation" not in current.collected_info
    handler, model = node(None)
    assert await handler(current) == AgentNode.ROUTE_DECISION
    model.classify_intent.assert_not_awaited()
    graph = object.__new__(AgentGraph)
    assert await route_decision_node(graph, current) == AgentNode.TOOL_EXECUTION
    assert current.current_business_parameters().reason == "商品外包装破损严重"
    assert current.current_business_parameters().order_id == "ORD940001"
    assert current.action_intent.request_type == "apply" and not current.action_intent.executable
    assert previous.missing_slots == ["reason"]


def test_new_topic_while_awaiting_reason_does_not_send_old_task():
    current = state("打印机如何安装", detected_intent="refund", missing_slots=["reason"],
                    collected_info={"order_id": "ORD940001"},
                    messages=[{"role": "user", "content": "旧退款订单ORD940001"}])
    payload = json.loads(input_payload(current))
    assert payload["known_order"] is None and payload["history"] == []


async def test_classifier_failure_and_cancellation_have_different_behavior():
    import asyncio

    handler, model = node(None)
    current = state("这个事情想了解一下")
    model.classify_intent.side_effect = RuntimeError("synthetic provider failure")
    assert await handler(current) == AgentNode.RESPONSE_GENERATION
    assert current.needs_clarification and not current.tool_calls
    model.classify_intent.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await handler(state("这个事情想了解一下"))


async def test_model_error_does_not_add_repair_requests():
    service = SimpleNamespace(run_structured=AsyncMock(side_effect=RuntimeError("synthetic")),
                              is_available=lambda: True)
    assert await understand(service, state("合成问题")) is None
    service.run_structured.assert_awaited_once()


async def test_another_question_does_not_get_an_order_clarification():
    current = state("我还有另一个问题：打印机如何安装", detected_intent="technical")
    assert await route_decision_node(object.__new__(AgentGraph), current) == AgentNode.QUERY_REWRITE


async def test_emotional_technical_question_main_graph_is_read_only(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent.query_rewrite import QueryRewriteResult

    invoke = AsyncMock(side_effect=AssertionError("维修咨询不能调用业务写工具"))
    monkeypatch.setattr("app.agent.nodes.tool_node.invoke_tool", invoke)
    graph = AgentGraph()
    graph.checkpointer = InMemorySaver()
    model = graph.llm_service
    model.classify_intent = AsyncMock(return_value=None)
    graph.llm_rewriter = SimpleNamespace(rewrite=AsyncMock(return_value=QueryRewriteResult(
        "这耳机太垃圾了，怎么连接电脑", "耳机连接电脑", True, "synthetic")))
    search = AsyncMock(return_value={"success": True, "results": [], "meta": {}})
    monkeypatch.setattr("app.agent.graph.get_knowledge_base", lambda: SimpleNamespace(async_search=search))
    graph._register_nodes()
    graph.langgraph = graph._build_langgraph()
    try:
        current = await graph.run(state("这耳机太垃圾了，怎么连接电脑", understanding_mode="rules"))
        assert current.current_node == AgentNode.END and current.detected_intent == "technical"
        assert current.user_emotion == "negative" and current.action_intent.request_type == "inquiry"
        assert not current.need_human
        search.assert_awaited_once()
        invoke.assert_not_awaited()
    finally:
        await model.close()


def test_resolved_model_inquiry_clears_previous_clarification_and_missing_slots():
    from app.agent.request_understanding import route_understanding

    current = state("耳机退款政策是什么", needs_clarification=True, missing_slots=["order_id"])
    value = result(entities={"order_id": None, "product": "耳机"})
    assert route_understanding(current, value) == AgentNode.RAG_RETRIEVAL
    assert not current.needs_clarification and current.missing_slots == []


async def test_unknown_legacy_topic_still_falls_back_without_invalid_action_contract():
    current = state("合成旧数据", detected_intent="unknown-legacy-topic")
    assert await route_decision_node(object.__new__(AgentGraph), current) == AgentNode.RESPONSE_GENERATION
    assert current.action_intent is None
