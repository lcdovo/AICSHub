"""Replay captured synthetic failures; keep illegal fields rejected under action-specific schemas."""
import json
from pathlib import Path

import pytest

from app.agent.request_understanding import RequestUnderstanding, TaskRequestUnderstanding, validate_semantics, route_understanding
from app.agent.state import AgentState, AgentNode
from app.agent.task_contracts import NextStep, TaskDecision, decision_contract
from app.agent.task_loop import enter_task
from app.config.config import settings

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "acceptance/agent-task-loop/captured-failures-v1.json").read_bytes())


@pytest.fixture
def mixed_state(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_TASK_LOOP_ENABLED", True)
    monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_MODE", "model")
    return AgentState(session_id="mixed-capture", user_id=101, actor_id=101, source_message_id=17,
                      user_message=FIXTURE["mixed_message"], understanding_mode="model", task_loop_enabled=True)


def test_captured_inquiry_and_order_plan_enters_scoped_task_read(mixed_state):
    value = TaskRequestUnderstanding.model_validate(FIXTURE["mixed_understanding"])
    assert validate_semantics(value, mixed_state) == value
    assert enter_task(mixed_state, route_understanding(mixed_state, value)) == AgentNode.TASK_EXECUTION
    assert mixed_state.task_runtime.next_step.reads[0].order_id == "ORD960001"
    assert mixed_state.task_runtime.decisions == 0


@pytest.mark.parametrize("mutation", ["plain_schema", "disabled", "missing_subquery", "wrong_order", "history_read", "unobserved_order", "single"])
def test_mixed_read_exception_cannot_expand_plain_inquiry_or_authority(mixed_state, mutation):
    data = json.loads(json.dumps(FIXTURE["mixed_understanding"]))
    if mutation == "plain_schema":
        data.pop("task_kind")
        data.pop("continue_task")
        value = RequestUnderstanding.model_validate(data)
    else:
        if mutation == "disabled":
            mixed_state.task_loop_enabled = False
        elif mutation == "missing_subquery":
            data["subrequests"] = [{"topic": "refund", "request_type": "inquiry"}]
        elif mutation == "wrong_order":
            data["read_tasks"][0]["resource_id"] = "ORD960002"
        elif mutation == "history_read":
            data["read_tasks"] = [{"tool": "get_user_history", "resource_id": None}]
        elif mutation == "unobserved_order":
            data["entities"]["order_id"] = "ORD960002"
            data["read_tasks"][0]["resource_id"] = "ORD960002"
        elif mutation == "single":
            data["task_kind"] = "single"
        value = TaskRequestUnderstanding.model_validate(data)
    with pytest.raises(ValueError):
        validate_semantics(value, mixed_state)


@pytest.mark.parametrize("step", [
    {"action": "read", "reads": [{"tool": "query_order", "order_id": "ORD960001"}]},
    {"action": "search_knowledge", "knowledge_focus": "refund_policy"},
    {"action": "ask_user", "question": "diagnostic_feedback"},
    {"action": "prepare_refund"}, {"action": "finish"}, {"action": "suggest_handoff"},
])
def test_decision_union_preserves_execution_contract(step):
    decision = TaskDecision.model_validate({"step": step})
    assert decision.next_step() == NextStep.model_validate(step)


@pytest.mark.parametrize("capture", FIXTURE["invalid_decisions"], ids=lambda item: item["operation"])
def test_captured_inapplicable_fields_still_rejected(capture):
    with pytest.raises(ValueError):
        NextStep.model_validate(capture["output"])
    with pytest.raises(ValueError):
        TaskDecision.model_validate({"step": capture["output"]})


@pytest.mark.parametrize("value", [
    {"step": {"action": "apply_refund"}},
    {"step": {"action": "prepare_refund", "user_id": 202}},
    {"step": {"action": "finish"}, "sql": "select 1"},
    {"step": {"action": "finish", "reads": []}},
    {"step": {"action": "ask_user", "question": "已退款成功"}},
    {"step": {"action": "read", "reads": []}},
])
def test_decision_rejects_undeclared_fields_and_write_escape(value):
    with pytest.raises(ValueError):
        TaskDecision.model_validate(value).next_step()


def test_schema_exposes_action_specific_fields_and_no_knowledge_hint_on_ask():
    schema = TaskDecision.model_json_schema()
    assert schema["properties"]["step"]["discriminator"]["propertyName"] == "action"
    assert set(schema["$defs"]["AskDecision"]["properties"]) == {"action", "question"}
    assert set(schema["$defs"]["PrepareRefundDecision"]["properties"]) == {"action"}
    assert schema["$defs"]["AskDecision"]["additionalProperties"] is False


def test_completed_capability_is_absent_from_actual_provider_schema():
    schema = decision_contract(("finish", "ask_user", "suggest_handoff"))
    assert issubclass(schema, TaskDecision)
    definitions = schema.model_json_schema()["$defs"]
    assert "SearchDecision" not in definitions and "ReadDecision" not in definitions
    assert schema.model_validate({"step": {"action": "finish"}}).next_step().action == "finish"
    with pytest.raises(ValueError):
        schema.model_validate({"step": {"action": "search_knowledge", "knowledge_focus": "refund_policy"}})
