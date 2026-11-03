from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from app.agent.flow_contracts import FlowFlag, TurnFlow
from app.agent.state import AgentState, AgentNode
from app.agent.nodes.response_node import response_generation_node
from tests.test_chat_turns import graph_for_response


def test_flow_unknown_false_and_true_remain_distinct_and_fields_are_strict():
    state = AgentState(session_id="s", user_id=101, user_message="synthetic")
    assert state.flow_value(FlowFlag.GROUNDED) is None
    state.set_flow(rag_grounded=False)
    assert state.flow_value(FlowFlag.GROUNDED) is False
    state.set_flow(rag_grounded=True)
    assert state.flow_value(FlowFlag.GROUNDED) is True
    for fields in [{"rag_grounded": "true"}, {"answer_validated": 1}, {"user_id": 202}]:
        previous = state.flow
        with pytest.raises(ValidationError):
            state.set_flow(**fields)
        assert state.flow is previous
    with pytest.raises(TypeError, match="enum_required"):
        state.flow_value("rag_grounded")


def test_legacy_boolean_import_is_once_and_cannot_override_established_flow():
    state = AgentState(session_id="s", user_id=101, user_message="synthetic",
                       collected_info={"rag_grounded": True})
    assert state.flow_value(FlowFlag.GROUNDED) is True
    state.collected_info["rag_grounded"] = False
    assert state.flow_value(FlowFlag.GROUNDED) is True
    state.set_flow(rag_grounded=False)
    state.collected_info["rag_grounded"] = True
    assert state.flow_value(FlowFlag.GROUNDED) is False


def test_explicit_unknown_does_not_import_later_compatibility_assertion():
    state = AgentState(session_id="s", user_id=101, user_message="synthetic")
    state.set_flow(answer_validated=None)
    state.collected_info["answer_validated"] = True
    assert state.flow_value(FlowFlag.VALIDATED) is None


@pytest.mark.parametrize("key", [flag.value for flag in FlowFlag])
def test_legacy_strings_or_numbers_are_not_boolean_control_decisions(key):
    state = AgentState(session_id="s", user_id=101, user_message="synthetic", collected_info={key: "false"})
    assert state.flow_value(FlowFlag(key)) is None
    state.collected_info[key] = 1
    assert state.flow_value(FlowFlag(key)) is None


def test_next_turn_drops_flow_even_when_pending_parameters_are_kept():
    state = AgentState(session_id="s", user_id=101, user_message="确认",
                       pending_confirmations=["apply_refund"],
                       collected_info={"refund_preview": {"action_id": "synthetic"}})
    state.set_flow(handoff_attempted=True, answer_validated=True, rag_grounded=True,
                   answer_from_cache=True, rag_refused=True, smalltalk=True)
    fresh = state.for_next_turn(101, "新问题")
    assert fresh.flow == TurnFlow()
    assert not any(key in fresh.collected_info for key in (flag.value for flag in FlowFlag))
    assert fresh.collected_info["refund_preview"]["action_id"] == "synthetic"


async def test_string_handoff_marker_cannot_skip_independent_response_review(monkeypatch):
    monkeypatch.setattr("app.agent.nodes.response_node.settings.USER_MEMORY_ENABLED", False)
    graph = graph_for_response(passes=(False, False))
    state = AgentState(session_id="s", user_id=101, user_message="合成问题", detected_intent="technical",
                       collected_info={"handoff_attempted": "false", "answer_validated": "true"})
    graph._generate_response_with_llm = AsyncMock(return_value="合成拒绝草稿")
    assert await response_generation_node(graph, state) == AgentNode.HUMAN_HANDOFF
    assert len(graph.validator.responses) == 2
    assert state.flow_value(FlowFlag.VALIDATED) is False


def test_evidence_invalidation_clears_typed_cache_and_review_decisions():
    state = AgentState(session_id="s", user_id=101, user_message="synthetic")
    state.set_flow(rag_grounded=True, answer_from_cache=True, answer_validated=True)
    state.invalidate_evidence("synthetic-version-change")
    assert state.flow_value(FlowFlag.GROUNDED) is False
    assert state.flow_value(FlowFlag.CACHE_HIT) is None
    assert state.flow_value(FlowFlag.VALIDATED) is None
    state.collected_info["answer_validated"] = True
    assert state.flow_value(FlowFlag.VALIDATED) is None


def test_typed_flow_serialization_preserves_explicit_cleared_conclusion():
    state = AgentState(session_id="s", user_id=101, user_message="synthetic")
    state.set_flow(answer_validated=None, handoff_attempted=False)
    restored = AgentState.model_validate(state.model_dump())
    restored.collected_info.update(answer_validated=True, handoff_attempted=True)
    assert restored.flow_value(FlowFlag.VALIDATED) is None
    assert restored.flow_value(FlowFlag.HANDOFF_ATTEMPTED) is False


def test_sql_versioned_session_snapshot_does_not_persist_turn_flow_decisions():
    from app.agent.memory import SessionManager
    state = AgentState(session_id="s", user_id=101, session_version=1, user_message="synthetic")
    state.set_flow(answer_validated=True, answer_from_cache=True, handoff_attempted=True)
    manager = SessionManager()
    snapshot = manager._serialize_state(state)
    assert "flow" not in snapshot
    assert not any(flag.value in snapshot["collected_info"] for flag in FlowFlag)
    restored = manager._deserialize_state(snapshot)
    assert restored.flow == TurnFlow()
