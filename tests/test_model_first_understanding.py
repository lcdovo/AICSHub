"""模型理解候选：快捷事件、唯一语义结果与程序执行边界。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.agent.graph import AgentGraph
from app.agent.nodes.intent_node import IntentNode, IntentDependencies, RoutingNode, RoutingDependencies
from app.agent.nodes.response_node import is_smalltalk
from app.agent.nodes.tool_node import ToolExecutionNode, ToolExecutionDependencies, select_tools, is_refund_confirmed
from app.agent.nodes.tool_node import refund_cancelled
from app.agent.request_understanding import RequestUnderstanding, route_understanding, validate_semantics
from app.agent.state import AgentNode, AgentState
from app.agent.tools import ToolExecutionResult
from tests.test_m4_understanding import result
from tests.test_session_creation import session_api as session_api


def state(message, **kw):
    return AgentState(session_id="model-first", user_id=101, user_message=message,
                      understanding_mode="model", **kw)


def handler(value):
    recognizer = SimpleNamespace(recognize=Mock(side_effect=AssertionError("不能再调规则分类器")))
    service = SimpleNamespace(run_structured=AsyncMock(return_value=value),
                              classify_intent=AsyncMock(side_effect=AssertionError("不能再调旧模型分类器")),
                              is_available=lambda: True)
    dependencies = IntentDependencies(recognizer, lambda: service, is_refund_confirmed, refund_cancelled,
                                     is_smalltalk, lambda *_: None, SimpleNamespace(cancel=AsyncMock()))
    return IntentNode(dependencies), service


@pytest.mark.parametrize("message", ["你好", "谢谢", "再见", "多谢您帮忙"])
async def test_deterministic_smalltalk_never_resolves_model_or_rules(message):
    node, service = handler(None)
    current = state(message)
    assert await node(current) == AgentNode.RESPONSE_GENERATION
    assert current.detected_intent == "general"
    service.run_structured.assert_not_awaited()


@pytest.mark.parametrize("message", ["你好，耳机怎么连接", "谢谢，帮我查订单ORD940001"])
async def test_greeting_prefix_does_not_hide_real_request(message):
    node, service = handler(result(topic="technical", entities={"order_id": None, "product": None},
                                   entity_source="none", retrieval_query=message))
    assert await node(state(message)) == AgentNode.RAG_RETRIEVAL
    service.run_structured.assert_awaited_once()


async def test_model_order_query_never_reenters_regex_extractor():
    current = state("看看ORD940001到哪儿了")
    value = result(topic="query_order", request_type="query", entities={"order_id": "ORD940001", "product": None})
    route_understanding(current, validate_semantics(value, current))
    extract = Mock(side_effect=AssertionError("模型路径不能重新regex提参"))
    assert await RoutingNode(RoutingDependencies(extract))(current) == AgentNode.TOOL_EXECUTION
    invoke = AsyncMock(return_value=ToolExecutionResult(success=True, data={"order_id": "ORD940001"}))
    tools = ToolExecutionNode(ToolExecutionDependencies(extract, select_tools, is_refund_confirmed, invoke, None))
    assert await tools(current) == AgentNode.RESULT_VERIFICATION
    extract.assert_not_called()
    assert invoke.call_args.kwargs["order_id"] == "ORD940001"


def test_model_cancel_without_pending_action_does_not_retrieve_or_create():
    current = state("取消这次操作")
    value = result(request_type="cancel", entities={"order_id": None, "product": None}, entity_source="none")
    assert route_understanding(current, value) == AgentNode.RESPONSE_GENERATION
    assert current.needs_clarification and "取消" in current.reply
    assert not current.tool_calls


def test_apply_on_unsupported_topic_cannot_fall_into_business_tool_selection():
    current = state("请修改设备配置")
    value = result(topic="technical", request_type="apply", entities={"order_id": None, "product": None}, entity_source="none")
    assert route_understanding(current, value) == AgentNode.RESPONSE_GENERATION
    assert current.needs_clarification


def test_model_greeting_is_not_allowed_to_discard_a_business_request():
    current = state("帮我处理退款")
    value = result(request_type="greeting", entities={"order_id": None, "product": None}, entity_source="none")
    with pytest.raises(ValueError):
        validate_semantics(value, current)


def test_parameters_are_strict_and_must_have_visible_source():
    data = result().model_dump()
    data["parameters"] = {"reason": "凭空补造的退款原因"}
    value = RequestUnderstanding.model_validate(data)
    with pytest.raises(ValueError):
        validate_semantics(value, state("耳机 ORD940001"))
    data["parameters"] = {"reason": "真实原因", "user_id": 202}
    with pytest.raises(ValueError):
        RequestUnderstanding.model_validate(data)


async def test_model_missing_reason_is_asked_not_fabricated_from_apply_sentence():
    current = state("ORD940001现在帮我申请退款")
    value = result(request_type="apply", entities={"order_id": "ORD940001", "product": None})
    route_understanding(current, value)
    assert await RoutingNode(RoutingDependencies(Mock(side_effect=AssertionError("no regex"))))(current) == AgentNode.SLOT_FILLING
    assert "reason" in current.missing_slots and not current.pending_confirmations


async def test_model_reason_reaches_preview_without_legacy_reinterpretation():
    message = "ORD940001请办理售后，外包装破损严重"
    current = state(message)
    data = result(request_type="apply", entities={"order_id": "ORD940001", "product": None}).model_dump()
    data["parameters"] = {"reason": "外包装破损严重", "refund_type": "refund"}
    value = RequestUnderstanding.model_validate(data)
    route_understanding(current, validate_semantics(value, current))
    extract = Mock(side_effect=AssertionError("no regex"))
    assert await RoutingNode(RoutingDependencies(extract))(current) == AgentNode.TOOL_EXECUTION
    tool = ToolExecutionNode(ToolExecutionDependencies(extract, select_tools, is_refund_confirmed,
                                                      AsyncMock(side_effect=AssertionError("未确认不能提交")), None))
    assert await tool(current) == AgentNode.CONFIRMATION
    assert current.refund_preview_data()["reason"] == "外包装破损严重"
    assert not current.tool_calls


def test_switching_order_does_not_reuse_previous_reason():
    current = state("ORD940002现在申请退款", collected_info={"order_id": "ORD940001", "reason": "旧订单破损原因"})
    value = result(request_type="apply", entities={"order_id": "ORD940002", "product": None})
    route_understanding(current, validate_semantics(value, current))
    assert current.current_business_parameters().reason is None


async def test_model_failure_clarifies_without_falling_back_to_legacy_action():
    node, service = handler(None)
    current = state("ORD940001请帮我申请退款")
    assert await node(current) == AgentNode.RESPONSE_GENERATION
    assert current.needs_clarification and service.run_structured.await_count == 2
    assert not current.pending_confirmations and not current.tool_calls


@pytest.mark.parametrize("message,expected", [("确认", AgentNode.ROUTE_DECISION), ("取消", AgentNode.RESPONSE_GENERATION)])
async def test_pending_action_events_do_not_use_semantic_model(message, expected):
    node, service = handler(None)
    current = state(message, collected_info={"refund_preview": {"action_id": "synthetic-action"}})
    assert await node(current) == expected
    service.run_structured.assert_not_awaited()
    service.classify_intent.assert_not_awaited()


@pytest.mark.parametrize("field,new_value", [
    ("user_message", "查询ORD940002"), ("user_id", 202), ("actor_id", 999),
    ("session_id", "other-session"), ("source_message_id", 99),
])
async def test_frozen_understanding_cannot_execute_after_scope_changes(field, new_value):
    current = state("查询ORD940001")
    value = result(topic="query_order", request_type="query", entities={"order_id": "ORD940001", "product": None})
    route_understanding(current, validate_semantics(value, current))
    setattr(current, field, new_value)
    invoke = AsyncMock(side_effect=AssertionError("scope changed"))
    tool = ToolExecutionNode(ToolExecutionDependencies(Mock(side_effect=AssertionError("no regex")),
                                                     select_tools, is_refund_confirmed, invoke, None))
    assert await tool(current) == AgentNode.RESPONSE_GENERATION
    assert current.needs_clarification
    invoke.assert_not_awaited()


async def test_selected_reason_cannot_be_replaced_before_preview():
    current = state("ORD940001退款，外包装破损严重，不是因为价格太贵")
    data = result(request_type="apply", entities={"order_id": "ORD940001", "product": None}).model_dump()
    data["parameters"] = {"reason": "外包装破损严重"}
    value = RequestUnderstanding.model_validate(data)
    route_understanding(current, validate_semantics(value, current))
    current.set_business_parameters(reason="价格太贵")
    invoke = AsyncMock(side_effect=AssertionError("reason changed"))
    tool = ToolExecutionNode(ToolExecutionDependencies(Mock(side_effect=AssertionError("no regex")),
                                                     select_tools, is_refund_confirmed, invoke, None))
    assert await tool(current) == AgentNode.RESPONSE_GENERATION
    assert not current.refund_preview_data()
    invoke.assert_not_awaited()


async def test_structured_routing_does_not_call_language_policy(monkeypatch):
    from app.agent.routing_policy import RoutingPolicy

    policy = RoutingPolicy()
    monkeypatch.setattr(policy, "decide", Mock(side_effect=AssertionError("no language routing")))
    monkeypatch.setattr(policy, "request_kind", Mock(side_effect=AssertionError("no keyword selection")))
    current = state("查看ORD940001")
    value = result(topic="query_order", request_type="query", entities={"order_id": "ORD940001", "product": None})
    route_understanding(current, validate_semantics(value, current))
    assert await RoutingNode(RoutingDependencies(Mock(side_effect=AssertionError("no regex")), policy))(current) == AgentNode.TOOL_EXECUTION


def test_understanding_context_is_not_carried_to_next_turn():
    current = state("查询ORD940001")
    value = result(topic="query_order", request_type="query", entities={"order_id": "ORD940001", "product": None})
    route_understanding(current, value)
    fresh = current.for_next_turn(101, "新问题")
    assert fresh.understanding is None and fresh.understanding_context is None


def test_understanding_receives_pending_slot_context_without_old_control_values():
    from app.agent.request_understanding import input_payload
    import json

    current = state("外包装破损严重", detected_intent="refund", missing_slots=["reason"],
                    collected_info={"order_id": "ORD940001", "confirm_refund": True})
    payload = json.loads(input_payload(current))
    assert payload["unfinished_topic"] == "refund" and payload["missing_slots"] == ["reason"]
    assert "confirm_refund" not in payload and payload["known_order"] == "ORD940001"


@pytest.mark.parametrize("transport", ["rest", "ws"])
@pytest.mark.parametrize("request_type", ["inquiry", "apply"])
def test_actual_api_uses_one_model_result_and_only_prepares_before_confirmation(session_api, monkeypatch, transport, request_type):
    import asyncio
    from sqlalchemy import select, func
    from langgraph.checkpoint.memory import InMemorySaver
    from app.config.config import settings
    from app.models.models import Order, RefundAction, RefundApplication, Ticket, HandoffRequestRecord, ChatRun
    from app.security import create_access_token
    from tests.test_chat_auth import receive_turn
    import app.routers.chat as chat

    client, factory, _, _, users = session_api
    message = ("你好，ORD940001请帮我申请退款，外包装破损严重" if request_type == "apply"
               else "你好，ORD940001退款多久到账")
    data = result(request_type=request_type, entities={"order_id": "ORD940001", "product": None},
                  retrieval_query=message).model_dump()
    if request_type == "apply":
        data["parameters"] = {"reason": "外包装破损严重", "refund_type": "refund"}
    value = RequestUnderstanding.model_validate(data)
    monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_MODE", "model")
    monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_PERCENTAGE", 100)
    graph = AgentGraph()
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    model = graph.llm_service
    model.run_structured = AsyncMock(return_value=value)
    graph.intent_recognizer.recognize = Mock(side_effect=AssertionError("model must not use legacy classifier"))
    monkeypatch.setattr(chat.chat_service, "process", graph.run)
    monkeypatch.setattr(chat.chat_service, "process_stream", graph.run_stream)
    monkeypatch.setattr("app.agent.graph.get_knowledge_base", lambda: SimpleNamespace(
        async_search=AsyncMock(return_value={"success": True, "results": [], "meta": {}})))

    async def seed():
        async with factory() as db:
            db.add(Order(order_id="ORD940001", user_id=101, status="已发货", status_code="shipped",
                         total_amount="0.10", items=[]))
            await db.commit()

    asyncio.run(seed())
    sid = client.post("/api/v1/chat/sessions?user_id=101").json()["data"]["id"]
    body = {"user_id": 101, "session_id": sid, "message": message, "client_message_id": "model-single-result"}
    try:
        if transport == "rest":
            response = client.post("/api/v1/chat/send", json=body)
            assert response.status_code == 200, response.text
        else:
            with client.websocket_connect("/api/v1/chat/stream?access_token=" + create_access_token(users[0])) as ws:
                ws.send_json(body)
                assert receive_turn(ws)[-1]["type"] == "stream_end"

        async def inspect():
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(RefundAction)) == (1 if request_type == "apply" else 0)
                for entity in [RefundApplication, Ticket, HandoffRequestRecord]:
                    assert await db.scalar(select(func.count()).select_from(entity)) == 0
                run = await db.scalar(select(ChatRun))
                assert run.status == ("awaiting_confirmation" if request_type == "apply" else "succeeded")
                assert run.request_data["_understanding_mode"] == "model"
                if request_type == "apply":
                    preview = await db.scalar(select(RefundAction))
                    assert preview.reason == "外包装破损严重" and preview.status == "pending"
        asyncio.run(inspect())
        model.run_structured.assert_awaited_once()
    finally:
        asyncio.run(model.close())
