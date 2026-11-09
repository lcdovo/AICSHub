"""Exercise the actual task executor/decision seam, without provider calls."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config.config import settings
from app.agent.state import AgentNode
from app.agent.task_loop import TaskDecisionNode, observe_task
from tests.test_intent_items import current, execution, knowledge, item_mode
from tests.test_agent_task_loop import reader, local_config


async def test_mandatory_item_search_skips_model_and_preserves_review(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_TASK_DETERMINISTIC_ROUTING_ENABLED", True)
    state = current()
    reader(monkeypatch)
    node, kb = execution([knowledge("退货需保留包装。", "pack"), knowledge("质量问题商家承担运费。", "shipping")])
    await node(state)
    await observe_task(state)
    model = SimpleNamespace(run_structured=AsyncMock(side_effect=AssertionError("redundant model call")))
    decision = TaskDecisionNode(lambda: model)
    assert await decision(state) == AgentNode.TASK_EXECUTION
    assert state.task_runtime.next_step.action == "search_knowledge"
    assert state.task_runtime.decisions == 0
    await node(state)
    await observe_task(state)
    assert await decision(state) == AgentNode.TASK_EXECUTION
    assert state.task_runtime.next_step.action == "finish"
    assert state.task.status != "completed" and not state.task_runtime.item_reviews
    assert kb.async_search.await_count == 2
    model.run_structured.assert_not_awaited()


@pytest.mark.parametrize("failed", [True, False])
async def test_failed_or_ambiguous_steps_retain_model_path(monkeypatch, failed):
    monkeypatch.setattr(settings, "AGENT_TASK_DETERMINISTIC_ROUTING_ENABLED", True)
    state = current()
    reader(monkeypatch, success=not failed)
    node, _ = execution([])
    await node(state)
    await observe_task(state)
    if not failed:
        # Additional unperformed read prevents deterministic scheduling.
        monkeypatch.setattr("app.agent.task_loop.allowed_actions", lambda _: ["read", "search_knowledge", "ask_user"])
    from app.agent.task_contracts import decision_contract
    contract = decision_contract(("ask_user",))
    model = SimpleNamespace(run_structured=AsyncMock(return_value=contract.model_validate(
        {"step": {"action": "ask_user", "question": "request"}})))
    await TaskDecisionNode(lambda: model)(state)
    model.run_structured.assert_awaited_once()


async def test_deterministic_routing_rejects_changed_turn_scope(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_TASK_DETERMINISTIC_ROUTING_ENABLED", True)
    state = current()
    state.user_message += "另一个请求"
    model = SimpleNamespace(run_structured=AsyncMock())
    assert await TaskDecisionNode(lambda: model)(state) == AgentNode.RESPONSE_GENERATION
    assert state.task.status == "stopped"
    model.run_structured.assert_not_awaited()
