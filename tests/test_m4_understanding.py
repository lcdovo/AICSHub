from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.agent.request_understanding import (
    RequestUnderstanding,
    understand,
    route_understanding,
)
from app.agent.state import AgentState, AgentNode


def result(**kw):
    data = dict(
        topic="refund",
        request_type="inquiry",
        entities={"order_id": "ORD940001", "product": "耳机"},
        conditions=[],
        subrequests=[],
        service_preference="unspecified",
        emotion="neutral",
        clarification=None,
        retrieval_query="耳机退款时效",
        entity_source="current_message",
    )
    data.update(kw)
    return RequestUnderstanding.model_validate(data)


def test_strict_schema_rejects_privilege_fields_bad_enum_and_type():
    for change in [
        {"user_id": 999},
        {"topic": "execute_code"},
        {"conditions": ["bad"]},
        {"entities": {"order_id": True}},
        {"request_type": "submit_sql"},
    ]:
        with pytest.raises(ValueError):
            result(**change)


@pytest.mark.parametrize(
    "message",
    [
        "退款多久到账 ORD940001 耳机",
        "不要退款 ORD940001 耳机",
        "客服说耳机能退是真的吗 ORD940001",
    ],
)
def test_inquiry_never_prepares_business_action(message):
    state = AgentState(session_id="s", user_id=101, user_message=message)
    assert route_understanding(state, result()) == AgentNode.RAG_RETRIEVAL
    assert not state.pending_confirmations and not state.tool_calls


async def test_model_gets_real_history_and_repairs_at_most_once():
    state = AgentState(
        session_id="s",
        user_id=101,
        user_message="它呢",
        messages=[{"role": "user", "content": "耳机订单ORD940001"}],
    )
    r = result(entity_source="history", request_type="inquiry")
    service = SimpleNamespace(
        run_structured=AsyncMock(side_effect=[None, r]), is_available=lambda: True
    )
    actual = await understand(service, state)
    assert actual == r and service.run_structured.await_count == 2
    payload = service.run_structured.call_args.args[1]
    assert (
        "ORD940001" in payload and "history" in payload and "current_message" in payload
    )


def test_conditional_request_queries_first_without_implicit_action():
    state = AgentState(
        session_id="s", user_id=101, user_message="先查耳机ORD940001 没发货就退款"
    )
    value = result(
        topic="query_order",
        request_type="query",
        conditions=[{"check": "order_not_shipped", "source": "current_message"}],
    )
    assert (
        route_understanding(state, value) == AgentNode.ROUTE_DECISION
        and state.detected_intent == "query_order"
    )
    assert not state.pending_confirmations and not state.collected_info.get(
        "refund_preview"
    )


def test_typed_state_is_not_reused_next_turn():
    state = AgentState(
        session_id="s",
        user_id=101,
        user_message="当前",
        understanding=result(),
        strategy_version="v1",
    )
    fresh = state.for_next_turn(101, "新消息")
    assert (
        fresh.understanding is None
        and fresh.node_steps == 0
        and fresh.strategy_version is None
    )


async def test_shadow_never_confirms_or_cancels_persistent_action(monkeypatch):
    from app.agent.nodes.intent_node import intent_recognition_node
    from app.config.config import settings

    monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_MODE", "shadow")
    graph = SimpleNamespace(
        llm_service=SimpleNamespace(
            run_structured=AsyncMock(return_value=result()), is_available=lambda: True
        )
    )
    for message in ["确认", "取消"]:
        state = AgentState(
            session_id="s",
            user_id=101,
            user_message=message,
            collected_info={"refund_preview": {"action_id": "original"}},
        )
        node = await intent_recognition_node(graph, state)
        assert node == AgentNode.RESPONSE_GENERATION and not state.tool_calls
        assert state.collected_info["refund_preview"]["action_id"] == "original"


async def test_read_plan_parallel_only_whitelist_and_principal():
    import asyncio
    from app.agent.read_plan import ReadTask, execute_read_plan

    for tool in ["apply_refund", "exec", "update_ticket_status"]:
        with pytest.raises(ValueError):
            ReadTask(tool=tool)
    with pytest.raises(ValueError):
        ReadTask(tool="query_order", user_id=999)
    count = 0
    started = asyncio.Event()

    async def reader(task, actor):
        nonlocal count
        count += 1
        if count == 2:
            started.set()
        await asyncio.wait_for(started.wait(), 1)
        return actor.id

    tasks = [
        ReadTask(tool="query_order", resource_id="ORD1"),
        ReadTask(tool="get_user_history"),
    ]
    assert await execute_read_plan(tasks, SimpleNamespace(id=101), reader) == [101, 101]


async def test_model_unavailable_ends_in_clarification_without_rule_action(monkeypatch):
    from app.agent.nodes.intent_node import intent_recognition_node
    from app.config.config import settings

    monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_MODE", "model")
    graph = SimpleNamespace(
        _is_smalltalk=lambda m: False,
        llm_service=SimpleNamespace(
            run_structured=AsyncMock(return_value=None), is_available=lambda: False
        ),
    )
    state = AgentState(session_id="s", user_id=101, user_message="我要退款")
    assert await intent_recognition_node(graph, state) == AgentNode.RESPONSE_GENERATION
    assert (
        state.needs_clarification
        and not state.pending_confirmations
        and not state.tool_calls
    )


@pytest.mark.parametrize(
    "topic,message",
    [
        ("refund", "耳机订单ORD940001请申请退款，不要改成维修"),
        ("human", "不想继续自动回复，请转真人"),
        ("complaint", "我要正式投诉，不是只问进度"),
    ],
)
def test_negation_of_other_service_does_not_veto_explicit_intent(topic, message):
    from app.agent.request_understanding import validate_semantics

    value = result(
        topic=topic,
        request_type="apply",
        entities={"order_id": None, "product": None},
        entity_source="none",
    )
    assert (
        validate_semantics(
            value, AgentState(session_id="s", user_id=101, user_message=message)
        )
        is value
    )


@pytest.mark.parametrize(
    "message", ["不要退款", "不想退款", "别给我退货", "不是申请，只问政策"]
)
def test_negated_refund_is_still_rejected(message):
    from app.agent.request_understanding import validate_semantics

    with pytest.raises(ValueError, match="negated_action"):
        validate_semantics(
            result(
                request_type="apply",
                entities={"order_id": None, "product": None},
                entity_source="none",
            ),
            AgentState(session_id="s", user_id=101, user_message=message),
        )


@pytest.mark.parametrize("source", ["none", "history"])
def test_entity_source_must_match_model_visible_context(source):
    from app.agent.request_understanding import validate_semantics

    state = AgentState(session_id="s", user_id=101, user_message="耳机 ORD940001")
    with pytest.raises(ValueError, match="entity_source"):
        validate_semantics(result(entity_source=source), state)


@pytest.mark.parametrize("history", [
    [{"role": "user", "content": "耳机 ORD940001"}]
    + [{"role": "user", "content": "别的背景"}] * 7,
    [{"role": "user", "content": "背景" * 300 + "耳机 ORD940001"}],
])
def test_entities_omitted_from_actual_model_input_cannot_validate(history):
    from app.agent.request_understanding import validate_semantics

    state = AgentState(session_id="s", user_id=101, user_message="它呢", messages=history)
    with pytest.raises(ValueError, match="unobserved_entity"):
        validate_semantics(result(entity_source="history"), state)


def test_known_order_actually_sent_to_model_can_validate_as_context_entity():
    from app.agent.request_understanding import validate_semantics

    state = AgentState(session_id="s", user_id=101, user_message="多久",
                       collected_info={"order_id": "ORD940001"})
    value = result(entity_source="history", entities={"order_id": "ORD940001", "product": None})
    assert validate_semantics(value, state) is value


def test_known_order_cannot_supply_an_unobserved_product():
    from app.agent.request_understanding import validate_semantics

    state = AgentState(session_id="s", user_id=101, user_message="多久",
                       collected_info={"order_id": "ORD940001"})
    value = result(entity_source="history", entities={"order_id": "ORD940001", "product": "ORD940001"})
    with pytest.raises(ValueError, match="unobserved_entity"):
        validate_semantics(value, state)


async def test_unseen_order_output_repairs_once_then_clarifies_without_action(monkeypatch):
    from app.agent.nodes.intent_node import intent_recognition_node
    from app.config.config import settings

    monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_MODE", "model")
    service = SimpleNamespace(
        run_structured=AsyncMock(return_value=result(request_type="apply", entity_source="history")),
        is_available=lambda: True,
    )
    graph = SimpleNamespace(_is_smalltalk=lambda _: False, llm_service=service)
    state = AgentState(session_id="s", user_id=101, user_message="我要退款",
                       messages=[{"role": "user", "content": "耳机 ORD940001"}]
                       + [{"role": "user", "content": "其他背景"}] * 7)
    assert await intent_recognition_node(graph, state) == AgentNode.RESPONSE_GENERATION
    assert service.run_structured.await_count == 2
    assert state.needs_clarification and not state.pending_confirmations and not state.tool_calls
    assert "order_id" not in state.collected_info
