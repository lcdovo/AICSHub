"""最终办理诉求与当前只读步骤分开；保留原真实冒烟失败，不重发。"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.agent.graph import AgentGraph
from app.agent.request_understanding import RequestUnderstanding, validate_semantics, route_understanding
from app.agent.state import AgentNode, AgentState
from app.agent.nodes.intent_node import RoutingNode, RoutingDependencies
from app.agent.nodes.tool_node import ToolExecutionNode, ToolExecutionDependencies, select_tools, is_refund_confirmed
from app.agent.tools import ToolExecutionResult

ROOT = Path(__file__).resolve().parents[1]


def captured():
    return json.loads((ROOT / "acceptance/model-first/conditional-captured-v1.json").read_bytes())


@pytest.mark.parametrize("with_plan", [True, False])
async def test_conditional_apply_can_only_execute_order_read(monkeypatch, with_plan):
    data = captured()
    value = RequestUnderstanding.model_validate(data["parsed"])
    if not with_plan:
        value.read_tasks = []
    current = AgentState(session_id="conditional", user_id=101, user_message=data["message"], understanding_mode="model")
    validate_semantics(value, current)
    next_node = route_understanding(current, value)
    if with_plan:
        assert next_node == AgentNode.TOOL_EXECUTION
    else:
        assert next_node == AgentNode.ROUTE_DECISION
        route = RoutingNode(RoutingDependencies(Mock(side_effect=AssertionError("no regex"))))
        assert await route(current) == AgentNode.TOOL_EXECUTION
    reads = AsyncMock(return_value=ToolExecutionResult(success=True, data={
        "order_id": "ORD970102", "status": "未发货", "status_code": "pending", "total_amount": "0.10"}))
    monkeypatch.setattr("app.agent.mcp_client.invoke_tool", reads)
    actions = SimpleNamespace(prepare=AsyncMock(side_effect=AssertionError("条件查询不得准备退款")))
    tool = ToolExecutionNode(ToolExecutionDependencies(Mock(side_effect=AssertionError("no regex")),
                                                     select_tools, is_refund_confirmed, reads, actions))
    assert await tool(current) == AgentNode.RESULT_VERIFICATION
    assert reads.await_count == 1 and reads.call_args.args[0] == "query_order"
    assert all(call.tool_name == "query_order" for call in current.tool_calls)
    assert not current.pending_confirmations and not current.refund_preview_data()
    assert current.action_intent.request_type == "apply" and not current.action_intent.executable
    actions.prepare.assert_not_awaited()


def test_conditional_apply_without_order_asks_before_any_execution():
    data = captured()
    value = RequestUnderstanding.model_validate(data["parsed"])
    value.entities.order_id = None
    value.entity_source = "none"
    value.read_tasks = []
    current = AgentState(session_id="s", user_id=101, user_message="如果没发货就帮我退款")
    validate_semantics(value, current)
    assert route_understanding(current, value) == AgentNode.RESPONSE_GENERATION
    assert current.needs_clarification and "order_id" in current.missing_slots
    assert not current.tool_calls and not current.pending_confirmations


def test_dropped_condition_still_cannot_become_unconditional_apply():
    data = captured()
    value = RequestUnderstanding.model_validate(data["parsed"])
    value.conditions = []
    value.read_tasks = []
    current = AgentState(session_id="s", user_id=101, user_message=data["message"])
    with pytest.raises(ValueError, match="conditional_write_without_read"):
        validate_semantics(value, current)


def test_policy_question_with_condition_does_not_query_private_order():
    value = RequestUnderstanding.model_validate(captured()["parsed"])
    value.request_type = "inquiry"
    value.read_tasks = []
    current = AgentState(session_id="s", user_id=101, user_message="订单ORD970102如果没发货退款政策是什么")
    validate_semantics(value, current)
    assert route_understanding(current, value) == AgentNode.RAG_RETRIEVAL
    assert not current.tool_calls and not current.pending_confirmations


def test_original_smoke_expectation_is_not_changed_to_make_old_run_pass():
    data = json.loads((ROOT / "acceptance/model-first/smoke-v1.json").read_bytes())
    case = next(item for item in data["cases"] if item["case_id"] == "smoke-004")
    assert case["expected"]["types"] == ["query"]
    assert captured()["original_smoke_pass"] is False


async def test_captured_real_output_full_graph_is_read_only(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    data = captured()
    value = RequestUnderstanding.model_validate(data["parsed"])
    graph = AgentGraph()
    service = graph.llm_service
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    monkeypatch.setattr(service, "run_structured", AsyncMock(return_value=value))
    reads = AsyncMock(return_value=ToolExecutionResult(success=True, data={
        "order_id": "ORD970102", "status": "未发货", "status_code": "pending", "total_amount": "0.10"}))
    monkeypatch.setattr("app.agent.mcp_client.invoke_tool", reads)
    try:
        current = await graph.run(AgentState(session_id="full-conditional", user_id=101,
                                             user_message=data["message"], understanding_mode="model"))
        assert current.current_node == AgentNode.END
        assert reads.await_count == 1 and reads.call_args.args[0] == "query_order"
        assert "申请尚未准备或提交" in current.reply
        assert not current.need_human and not current.pending_confirmations and not current.refund_preview_data()
        service.run_structured.assert_awaited_once()
    finally:
        await service.close()


@pytest.mark.parametrize("foreign", [False, True])
async def test_conditional_final_apply_keeps_actual_sql_owner_checks(tmp_path, monkeypatch, foreign):
    from sqlalchemy import select, func
    from langgraph.checkpoint.memory import InMemorySaver
    from tests.test_ticket_idempotency import api_context
    from app.models.models import Order, RefundAction, RefundApplication, Ticket, HandoffRequestRecord
    from app.agent.tools import QueryOrderTool

    data = captured()
    async with api_context(tmp_path / "conditional-owner.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        async with factory() as db:
            db.add(Order(order_id="ORD970102", user_id=202 if foreign else 101,
                         status="未发货", status_code="pending", total_amount="0.10", items=[]))
            await db.commit()
        calls = []

        async def invoke(name, **payload):
            calls.append(name)
            assert name == "query_order"
            result = await QueryOrderTool().execute(**payload)
            return ToolExecutionResult(**{key: val for key, val in result.items()
                                         if key in ToolExecutionResult.model_fields})

        monkeypatch.setattr("app.agent.mcp_client.invoke_tool", invoke)
        graph = AgentGraph()
        graph.checkpointer = InMemorySaver()
        graph.langgraph = graph._build_langgraph()
        service = graph.llm_service
        monkeypatch.setattr(service, "run_structured", AsyncMock(return_value=RequestUnderstanding.model_validate(data["parsed"])))
        try:
            current = await graph.run(AgentState(session_id="session-101", user_id=101, actor_id=101,
                                                 user_message=data["message"], understanding_mode="model"))
            assert current.current_node == AgentNode.END and calls == ["query_order"]
            assert current.tool_calls[0].success is not foreign
            if foreign:
                assert "0.10" not in current.reply and "未发货" not in current.reply
            assert not current.pending_confirmations and not current.refund_preview_data()
            async with factory() as db:
                for entity in [RefundAction, RefundApplication, Ticket, HandoffRequestRecord]:
                    assert await db.scalar(select(func.count()).select_from(entity)) == 0
        finally:
            await service.close()


def test_conditional_read_plan_cannot_skip_order_prerequisite():
    data = captured()
    value = RequestUnderstanding.model_validate(data["parsed"])
    from app.agent.read_plan import ReadTask
    value.read_tasks = [ReadTask(tool="get_user_history")]
    current = AgentState(session_id="s", user_id=101, user_message=data["message"])
    with pytest.raises(ValueError, match="conditional_order_read_required"):
        validate_semantics(value, current)


def test_final_refund_reason_question_does_not_block_current_prerequisite_read():
    data = captured()
    value = RequestUnderstanding.model_validate(data["parsed"])
    value.clarification = "请补充退款原因。"
    value.missing_information = ["reason"]
    current = AgentState(session_id="s", user_id=101, user_message=data["message"])
    validate_semantics(value, current)
    assert route_understanding(current, value) == AgentNode.TOOL_EXECUTION
    assert not current.needs_clarification and not current.pending_confirmations


def test_explicit_uncertain_order_blocks_even_a_proposed_read_plan():
    data = captured()
    value = RequestUnderstanding.model_validate(data["parsed"])
    value.clarification = None
    value.missing_information = ["order_id", "reason"]
    current = AgentState(session_id="s", user_id=101, user_message=data["message"])
    validate_semantics(value, current)
    assert route_understanding(current, value) == AgentNode.RESPONSE_GENERATION
    assert current.needs_clarification and "order_id" in current.missing_slots
    assert not current.read_plan and not current.tool_calls
