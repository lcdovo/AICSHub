"""有界任务调度、模型失效、证据与确认边界；不发真实模型请求。"""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.agent.request_understanding import TaskRequestUnderstanding, route_understanding
from app.agent.state import AgentNode, AgentState
from app.agent.task_contracts import NextStep, TaskDecision
from app.agent.task_loop import (TaskDecisionNode, TaskExecutionNode, enter_task, observe_task,
                                 allowed_actions, conditions_met, knowledge_query, decision_payload)
from app.agent.tools import ToolExecutionResult
from app.agent.nodes.rag_node import RetrievalNode, RetrievalDependencies
from app.config.config import settings
from tests.test_m4_evidence_contract import result as knowledge_result


@pytest.fixture(autouse=True)
def local_config(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_TASK_LOOP_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_TASK_REFUND_PREVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "REDIS_ENABLED", False)
    monkeypatch.setattr(settings, "USER_MEMORY_ENABLED", False)
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", False)


def request_state(*, kind="after_sales", message=None, **changes):
    fields = dict(topic="refund", request_type="apply", entities={"order_id": "ORD960001", "product": None},
        conditions=[{"check": "order_delivered", "source": "current_message"}],
        subrequests=[{"topic": "query_order", "request_type": "query"}, {"topic": "refund", "request_type": "apply"}],
        service_preference="unspecified", emotion="neutral", clarification=None,
        retrieval_query="订单ORD960001的退货退款条件", entity_source="current_message", read_tasks=[],
        parameters={"reason": "外壳明显破损", "refund_type": "return"}, missing_information=[], task_kind=kind)
    fields.update(changes)
    value = TaskRequestUnderstanding.model_validate(fields)
    message = message or "请查询订单ORD960001，如果已签收，请申请退货退款，外壳明显破损。"
    state = AgentState(session_id="session-101", user_id=101, actor_id=101, source_message_id=17,
                       user_message=message, understanding_mode="model", task_loop_enabled=True)
    default = route_understanding(state, value)
    return state, enter_task(state, default)


def executor(*, result=None, actions=None, current=True):
    kb = SimpleNamespace(async_search=AsyncMock(return_value=result or knowledge_result()),
                         evidence_is_current=Mock(return_value=current))
    retrieval = RetrievalNode(RetrievalDependencies(lambda: kb, lambda: 3, .35))
    actions = actions or SimpleNamespace(prepare=AsyncMock(side_effect=AssertionError("no preview expected")))
    return TaskExecutionNode(retrieval, actions), kb


def reader(monkeypatch, *, success=True, status="delivered"):
    call = AsyncMock(return_value=[({"user_id": 101, "order_id": "ORD960001"},
        ToolExecutionResult(success=success, data={"order_id": "ORD960001", "status_code": status,
                            "status": "已签收", "total_amount": "0.10"} if success else None,
                            error_type=None if success else "permission"))])
    monkeypatch.setattr("app.agent.read_plan.run_authorized_reads", call)
    return call


async def read_and_search(state, node):
    assert await node(state) == AgentNode.TASK_OBSERVATION
    assert await observe_task(state) == AgentNode.TASK_DECISION
    state.task_runtime.next_step = NextStep(action="search_knowledge", knowledge_focus="refund_policy")
    assert await node(state) == AgentNode.TASK_OBSERVATION
    assert await observe_task(state) == AgentNode.TASK_DECISION


def test_initial_step_reuses_understanding_and_keeps_original_goal():
    state, next_node = request_state()
    assert next_node == AgentNode.TASK_EXECUTION
    assert state.task_runtime.decisions == 0
    assert state.task_runtime.next_step.reads[0].order_id == "ORD960001"
    assert state.task.kind == "after_sales" and state.task.goal == state.user_message


@pytest.mark.parametrize("fields", [
    {"action": "apply_refund"}, {"action": "read", "user_id": 202},
    {"action": "read", "reads": [{"tool": "query_order", "order_id": "ORD960001"}], "sql": "select 1"},
    {"action": "read", "reads": [{"tool": "get_user_history", "order_id": "202"}]},
    {"action": "finish", "reads": [{"tool": "query_order"}]},
    {"action": "finish", "question": "reason"},
    {"action": "ask_user", "question": "已退款成功"},
    {"action": "search_knowledge", "knowledge_focus": "request", "authorization": "forged"},
])
def test_model_schema_has_no_write_or_identity_escape(fields):
    with pytest.raises(ValueError):
        NextStep.model_validate(fields)


async def test_conditional_task_reads_policy_then_prepares_only(monkeypatch):
    call = reader(monkeypatch)
    action = {"action_id": "synthetic-action", "version": 1, "order_id": "ORD960001",
              "type": "return", "reason": "外壳明显破损", "amount": "0.10", "currency": "CNY",
              "status": "pending", "expires_at": (datetime.utcnow() + timedelta(minutes=10)).isoformat(),
              "application_id": None, "is_simulated": True}
    actions = SimpleNamespace(prepare=AsyncMock(return_value=action))
    node, kb = executor(actions=actions)
    state, _ = request_state()
    await read_and_search(state, node)
    assert "prepare_refund" in allowed_actions(state)
    state.task_runtime.next_step = NextStep(action="prepare_refund")
    assert await node(state) == AgentNode.CONFIRMATION
    assert state.task.status == "waiting_confirmation"
    assert state.pending_confirmations == ["apply_refund"]
    assert not state.has_current_refund_confirmation()
    assert all(c.tool_name != "apply_refund" for c in state.tool_calls)
    assert actions.prepare.await_count == 1 and call.await_count == 1 and kb.async_search.await_count == 1


@pytest.mark.parametrize("status", ["pending", "shipped", "unknown"])
async def test_unmet_condition_stops_before_decision_or_preview(monkeypatch, status):
    reader(monkeypatch, status=status)
    state, _ = request_state()
    node, _ = executor()
    assert await node(state) == AgentNode.TASK_OBSERVATION
    assert await observe_task(state) == AgentNode.RESPONSE_GENERATION
    assert state.task.stop_reason == "condition_not_satisfied"
    node.actions.prepare.assert_not_awaited()


async def test_no_read_or_no_current_policy_cannot_prepare(monkeypatch):
    reader(monkeypatch)
    state, _ = request_state()
    node, _ = executor()
    state.task_runtime.next_step = NextStep(action="prepare_refund")
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.task.status == "stopped"
    node.actions.prepare.assert_not_awaited()


async def test_multi_request_cannot_finish_after_knowledge_without_requested_order(monkeypatch):
    state, _ = request_state(kind="multi", topic="refund", request_type="inquiry",
        message="退货需要保留包装吗？再查询订单ORD960001。", conditions=[], parameters={},
        subrequests=[{"topic": "refund", "request_type": "inquiry"},
                     {"topic": "query_order", "request_type": "query"}])
    node, kb = executor()
    assert await node(state) == AgentNode.TASK_OBSERVATION
    assert state.current_evidence() and kb.async_search.await_count == 1
    assert "finish" not in allowed_actions(state)
    state.task_runtime.next_step = NextStep(action="finish")
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.task.status == "stopped"


async def test_readonly_rollout_blocks_refund_preview_even_with_complete_evidence(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_TASK_REFUND_PREVIEW_ENABLED", False)
    reader(monkeypatch)
    state, _ = request_state()
    node, _ = executor()
    await read_and_search(state, node)
    assert conditions_met(state) and state.current_evidence()
    assert "prepare_refund" not in allowed_actions(state)
    state.task_runtime.next_step = NextStep(action="prepare_refund")
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.task.status == "stopped" and not state.refund_preview_data()
    node.actions.prepare.assert_not_awaited()


@pytest.mark.parametrize("invalidate", [False, True])
async def test_duplicate_diagnostic_search_uses_only_current_evidence_for_feedback(monkeypatch, invalidate):
    state, _ = request_state(kind="troubleshoot", topic="technical", request_type="inquiry",
        message="耳机无法开机，请一步步帮我排查。", entities={"order_id": None, "product": "耳机"},
        conditions=[], subrequests=[], parameters={})
    node, kb = executor()
    assert await node(state) == AgentNode.TASK_OBSERVATION
    assert await observe_task(state) == AgentNode.TASK_DECISION
    if invalidate:
        state.invalidate_evidence("synthetic_evidence_lost")
    state.task_runtime.next_step = NextStep(action="search_knowledge", knowledge_focus="troubleshooting")
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert kb.async_search.await_count == 1 and state.task_runtime.tool_calls == 1
    if invalidate:
        assert state.task.status == "stopped"
    else:
        assert state.task.status == "waiting_input" and state.task.waiting_for == "diagnostic_feedback"
        assert state.task_runtime.response_goal == "diagnostic_feedback"
        assert any(step.status == "blocked" and step.error_code == "duplicate_search_feedback"
                   for step in state.task_runtime.steps)
    assert not state.pending_confirmations and not state.need_human


async def test_duplicate_after_sales_search_still_stops_without_refund_preview(monkeypatch):
    reader(monkeypatch)
    state, _ = request_state()
    node, kb = executor()
    await read_and_search(state, node)
    state.task_runtime.next_step = NextStep(action="search_knowledge", knowledge_focus="refund_policy")
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.task.status == "stopped" and kb.async_search.await_count == 1
    node.actions.prepare.assert_not_awaited()


async def test_completed_mixed_capabilities_are_not_offered_again(monkeypatch):
    reader(monkeypatch)
    state, _ = request_state(kind="multi", topic="query_order", request_type="query", conditions=[], parameters={},
        subrequests=[{"topic": "query_order", "request_type": "query"},
                     {"topic": "refund", "request_type": "inquiry"}])
    node, _ = executor()
    await read_and_search(state, node)
    allowed = allowed_actions(state)
    assert "finish" in allowed and "read" not in allowed and "search_knowledge" not in allowed


async def test_policy_focus_keeps_packaging_question_and_exposes_actual_evidence(monkeypatch):
    reader(monkeypatch)
    state, _ = request_state(kind="multi", topic="query_order", request_type="query", conditions=[], parameters={},
        retrieval_query="退货是否需要保留包装", subrequests=[{"topic": "query_order", "request_type": "query"},
                     {"topic": "refund", "request_type": "inquiry"}])
    assert "包装" in knowledge_query(state, "refund_policy")
    node, _ = executor()
    await read_and_search(state, node)
    payload = decision_payload(state)
    evidence = next(item["data"] for item in payload["observations"] if item["capability"] == "hybrid_search")
    assert isinstance(evidence, dict) and evidence["knowledge_acquired"]
    assert evidence["items"][0]["content"] == state.current_evidence()[0]["content"]
    assert "search_knowledge:refund_policy" in payload["completed_capabilities"]


async def test_after_sales_still_offers_policy_after_unrelated_knowledge(monkeypatch):
    reader(monkeypatch)
    state, _ = request_state()
    node, _ = executor()
    await node(state)
    await observe_task(state)
    state.task_runtime.next_step = NextStep(action="search_knowledge", knowledge_focus="request")
    await node(state)
    await observe_task(state)
    assert "search_knowledge" in allowed_actions(state) and "prepare_refund" not in allowed_actions(state)


async def test_missing_reason_asks_without_preparing(monkeypatch):
    reader(monkeypatch)
    state, _ = request_state(parameters={"refund_type": "return"}, missing_information=["reason"])
    node, _ = executor()
    await read_and_search(state, node)
    assert "prepare_refund" not in allowed_actions(state)
    state.task_runtime.next_step = NextStep(action="ask_user", question="reason")
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.task.status == "waiting_input" and state.task.waiting_for == "reason"
    assert state.missing_slots == ["reason"] and "原因" in state.reply
    node.actions.prepare.assert_not_awaited()


async def test_short_reason_does_not_bypass_shared_slot_policy(monkeypatch):
    reader(monkeypatch)
    state, _ = request_state(parameters={"reason": "外壳破损", "refund_type": "return"})
    node, _ = executor()
    await read_and_search(state, node)
    assert "prepare_refund" not in allowed_actions(state)


@pytest.mark.parametrize("mutate", ["goal", "conditions"])
async def test_task_goal_cannot_be_changed_between_decision_and_execution(monkeypatch, mutate):
    call = reader(monkeypatch)
    state, _ = request_state()
    from app.agent.task_loop import progress
    progress(state, **({"goal": "改成别的办理事项"} if mutate == "goal" else {"conditions": []}))
    node, _ = executor()
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    call.assert_not_awaited()


async def test_direct_internal_node_without_task_stops_safely():
    state = AgentState(session_id="s", user_id=101, user_message="合成问题")
    node, _ = executor()
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.needs_clarification


def test_configuration_requires_model_mode_and_sql_gateway(monkeypatch):
    from app.config.config import Settings
    monkeypatch.delenv("AGENT_TASK_LOOP_ENABLED", raising=False)
    with pytest.raises(ValueError, match="任务循环要求"):
        Settings(_env_file=None, AGENT_TASK_LOOP_ENABLED=True, REQUEST_UNDERSTANDING_MODE="model", LLM_GATEWAY_ENABLED=False)
    valid = Settings(_env_file=None, AGENT_TASK_LOOP_ENABLED=True, REQUEST_UNDERSTANDING_MODE="model", LLM_GATEWAY_ENABLED=True)
    assert valid.AGENT_TASK_MAX_DECISIONS == 3


async def test_stale_knowledge_is_checked_again_before_preview(monkeypatch):
    reader(monkeypatch)
    state, _ = request_state()
    node, _ = executor(current=False)
    await read_and_search(state, node)
    state.task_runtime.next_step = NextStep(action="prepare_refund")
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.task.stop_reason == "knowledge_version_changed"
    assert not state.current_evidence()
    node.actions.prepare.assert_not_awaited()


async def test_unknown_preview_is_not_retried_or_called_submitted(monkeypatch):
    reader(monkeypatch)
    state, _ = request_state()
    actions = SimpleNamespace(prepare=AsyncMock(side_effect=OSError("synthetic unknown")))
    node, _ = executor(actions=actions)
    await read_and_search(state, node)
    state.task_runtime.next_step = NextStep(action="prepare_refund")
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.task.stop_reason == "refund_outcome_unknown"
    assert "不要重复" in state.reply
    assert actions.prepare.await_count == 1


async def test_duplicate_read_and_tool_budget_do_not_dispatch(monkeypatch):
    call = reader(monkeypatch)
    state, _ = request_state()
    node, _ = executor()
    await node(state)
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert call.await_count == 1
    state, _ = request_state()
    state.task_runtime.tool_calls = settings.AGENT_TASK_MAX_TOOL_CALLS
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert call.await_count == 1


@pytest.mark.parametrize("field,new", [("user_id", 202), ("actor_id", 999), ("session_id", "other"),
    ("source_message_id", 999), ("user_message", "改查另一单")])
async def test_identity_or_input_changes_cannot_dispatch(monkeypatch, field, new):
    call = reader(monkeypatch)
    state, _ = request_state()
    setattr(state, field, new)
    node, _ = executor()
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    call.assert_not_awaited()
    node.actions.prepare.assert_not_awaited()


async def test_failure_and_parallel_partial_success_stop_before_writes(monkeypatch):
    state, _ = request_state(read_tasks=[{"tool": "query_order", "resource_id": "ORD960001"}])
    state.task_runtime.next_step = NextStep(action="read", reads=[{"tool": "query_order", "order_id": "ORD960001"}])
    reader(monkeypatch, success=False)
    node, _ = executor()
    await node(state)
    assert await observe_task(state) == AgentNode.RESPONSE_GENERATION
    assert not state.need_human and not state.pending_confirmations


async def test_decision_budget_and_model_failure_never_fall_back_to_legacy():
    state, _ = request_state()
    model = SimpleNamespace(run_structured=AsyncMock(return_value=TaskDecision(step={"action": "finish"})))
    node = TaskDecisionNode(lambda: model)
    state.task_runtime.decisions = settings.AGENT_TASK_MAX_DECISIONS
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    model.run_structured.assert_not_awaited()
    state, _ = request_state()
    model.run_structured.side_effect = OSError("synthetic provider failure")
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    assert state.task.stop_reason == "decision_unavailable"
    assert model.run_structured.await_count == 1
    assert model.run_structured.call_args.kwargs["single_attempt"] is True


async def test_cancelled_decision_propagates():
    state, _ = request_state()
    node = TaskDecisionNode(lambda: SimpleNamespace(run_structured=AsyncMock(side_effect=asyncio.CancelledError())))
    with pytest.raises(asyncio.CancelledError):
        await node(state)


async def test_late_decision_cannot_execute_even_if_provider_swallows_cancellation():
    state, _ = request_state()
    state.task_runtime.deadline = __import__("time").monotonic() + .01

    async def late(*args, **kwargs):
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            return NextStep(action="prepare_refund")

    assert await TaskDecisionNode(lambda: SimpleNamespace(run_structured=late))(state) == AgentNode.RESPONSE_GENERATION
    assert state.task.status == "stopped"


def test_clean_turn_keeps_only_pending_progress_not_results_or_permission():
    state, _ = request_state()
    from app.agent.task_loop import progress
    progress(state, status="waiting_input", waiting_for="reason")
    state.task_runtime.next_step = NextStep(action="prepare_refund")
    state.record_tool_observation("query_order", {"success": True, "data": {"status_code": "delivered"}})
    fresh = state.for_next_turn(101, "外壳破损")
    assert fresh.task.task_id == state.task.task_id
    assert fresh.task_runtime is None and fresh.understanding is None and fresh.understanding_context is None
    assert not fresh.tool_calls and not fresh.tool_observations and not fresh.current_evidence()
    assert fresh.task_loop_enabled is None and fresh.run_id is None
    assert state.for_next_turn(202, "继续").task is None


def test_simple_question_and_disabled_loop_keep_original_path():
    state, node = request_state(kind="single", conditions=[], subrequests=[], topic="technical", request_type="inquiry",
        entities={"order_id": None, "product": "耳机"}, parameters={}, message="耳机如何连接手机")
    assert node == AgentNode.RAG_RETRIEVAL and state.task is None
    state, _ = request_state()
    state.task_loop_enabled = False
    assert enter_task(state, AgentNode.ROUTE_DECISION) == AgentNode.ROUTE_DECISION


@pytest.mark.parametrize("kind", ["single", "after_sales"])
def test_conditional_policy_inquiry_stays_rag_even_when_task_kind_is_wrong(kind):
    state, node = request_state(kind=kind, request_type="inquiry", subrequests=[], parameters={},
        message="订单ORD960001如果已签收，退货政策是什么？")
    assert node == AgentNode.RAG_RETRIEVAL and state.task is None


def test_apply_subrequest_cannot_make_policy_inquiry_authorize_preview():
    from app.agent.task_loop import refund_goal
    state, _ = request_state(kind="multi", request_type="inquiry", message="订单ORD960001不要申请退款，只咨询政策。")
    assert not refund_goal(state) and not state.task.refund_requested


async def test_single_attempt_structured_call_does_not_use_backup(monkeypatch):
    from app.services.llm_service import LLMService, ModelConfig
    service = LLMService()
    try:
        monkeypatch.setattr(service, "is_available", lambda: True)
        models = [ModelConfig(name="primary", api_key="synthetic"), ModelConfig(name="backup", api_key="synthetic")]
        monkeypatch.setattr(service, "_analytic_models", lambda **_: models)
        get_model = Mock(return_value=Mock())
        monkeypatch.setattr(service, "_get_chat_model", get_model)
        monkeypatch.setattr(service, "_gateway_invoke", AsyncMock(side_effect=OSError("synthetic")))
        assert await service.run_structured("test", "test", NextStep, single_attempt=True) is None
        assert get_model.call_count == 1 and get_model.call_args.args[0].name == "primary"
    finally:
        await service.close()
