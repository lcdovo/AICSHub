from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.agent.state import AgentState, AgentNode
from app.agent.nodes.response_node import ResponseNode, ResponseServices
from tests.test_chat_turns import Cache, Validator
from tests.test_m4_evidence_contract import result as retrieval


async def test_generation_header_does_not_retain_provider_reply_prompt_or_credentials():
    metadata = {"model": "synthetic", "tokens": 3, "degraded": False,
                "provider_usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                "structured_output": {"reply": "synthetic-private-answer"},
                "input_summary": "synthetic-private-user-input", "api_key": "synthetic-private-key"}
    model = SimpleNamespace(chat=AsyncMock(return_value=("合成独立审查回答", "technical", 3)),
                            get_last_call_metadata=lambda: metadata)
    cache = Cache()
    node = ResponseNode(ResponseServices(lambda: model, lambda: Validator(), lambda: None,
                                         lambda: cache, lambda: None, lambda: None))
    state = AgentState(session_id="s", user_id=101, user_message="合成问题", detected_intent="technical")
    assert await node(state) == AgentNode.END
    header = state.collected_info["llm_governance"]
    assert not any(key in header for key in ("structured_output", "input_summary", "api_key"))
    assert state.generation_metadata.model == "synthetic"
    assert state.generation_metadata.usage_basis == "provider_tokens"
    assert state.generation_metadata.provider_usage.input_tokens == 2
    assert not state.for_next_turn(101, "新问题").generation_metadata


@pytest.mark.parametrize("usage,expected", [
    ({}, "unknown"), ({"input_tokens": 2}, "partial_provider_tokens"),
    ({"input_tokens": 2, "output_tokens": 1}, "provider_tokens"),
])
def test_usage_completeness_is_explicit_without_filling_missing_output_with_zero(usage, expected):
    state = AgentState(session_id="s", user_id=101, user_message="合成问题")
    state.record_generation_metadata({"provider_usage": usage, "degraded": False})
    assert state.generation_metadata.usage_basis == expected
    if "output_tokens" not in usage:
        assert state.generation_metadata.provider_usage.output_tokens is None


@pytest.mark.parametrize("change", [
    {"tokens": -1}, {"latency_ms": "12"}, {"provider_usage": {"input_tokens": True}},
    {"cost_usd": float("nan")}, {"degraded": "false"},
    {"provider_usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 9}},
])
def test_invalid_metadata_is_unknown_and_degraded(change):
    state = AgentState(session_id="s", user_id=101, user_message="合成问题")
    assert not state.record_generation_metadata(change)
    assert state.generation_metadata.degraded
    assert state.generation_metadata.usage_basis == "unknown"
    assert state.generation_metadata.cost_usd is None
    assert state.generation_metadata_error == "generation_metadata_invalid"


def test_type_header_and_legacy_projection_are_independent_and_cannot_override_each_other():
    state = AgentState(session_id="s", user_id=101, user_message="合成问题")
    metadata = {"model": "synthetic", "provider_usage": {"input_tokens": 2, "output_tokens": 1}, "degraded": False}
    original = deepcopy(metadata)
    assert state.record_generation_metadata(metadata)
    metadata["provider_usage"]["input_tokens"] = 999
    state.collected_info["llm_governance"]["degraded"] = True
    state.collected_info["llm_governance"]["model"] = "forged"
    current = state.current_generation_metadata()
    assert current.model == original["model"] and not current.degraded
    assert current.provider_usage.input_tokens == 2


async def test_malformed_generation_metadata_does_not_cache_answer_even_when_review_passes(monkeypatch):
    monkeypatch.setattr("app.agent.nodes.response_node.settings.USER_MEMORY_ENABLED", False)
    cache = Cache()
    model = SimpleNamespace(chat=AsyncMock(return_value=("合成证据回答", "technical", 3)),
                            get_last_call_metadata=lambda: {"degraded": False, "cost_usd": "bad"})
    knowledge = SimpleNamespace(evidence_is_current=lambda _: True)
    judge = SimpleNamespace(DEFAULT_THRESHOLD=.45, judge=AsyncMock(return_value=None))
    node = ResponseNode(ResponseServices(lambda: model, lambda: Validator(), lambda: judge,
                                         lambda: cache, lambda: knowledge, lambda: None))
    state = AgentState(session_id="s", user_id=101, user_message="合成问题", detected_intent="technical")
    assert state.accept_retrieval(retrieval())
    state.set_flow(rag_grounded=True)
    assert await node(state) == AgentNode.END
    assert state.review_result.passed
    assert state.generation_metadata.degraded and cache.values == {}


def test_usage_label_is_derived_from_counts_and_header_roundtrips():
    state = AgentState(session_id="s", user_id=101, user_message="合成问题")
    assert state.record_generation_metadata({"usage_basis": "provider_tokens", "provider_usage": {"input_tokens": 2}})
    assert state.generation_metadata.usage_basis == "partial_provider_tokens"
    restored = AgentState.model_validate(state.model_dump())
    assert restored.generation_metadata == state.generation_metadata
    assert not restored.for_next_turn(101, "新问题").generation_metadata


async def test_evaluation_and_trace_use_typed_header_even_if_compatibility_dictionary_is_changed(tmp_path):
    from sqlalchemy import select
    from app.models.models import Session, EvaluationResult, AgentTrace
    from app.services.chat_service import ChatService
    from tests.test_ticket_idempotency import api_context

    async with api_context(tmp_path / "metadata.db") as (_, factory):
        state = AgentState(session_id="session-101", user_id=101, user_message="合成问题", reply="合成回答")
        state.record_generation_metadata({"model": "synthetic-primary", "prompt_version": "synthetic-v1",
                                          "schema_version": "synthetic-schema", "route_version": "synthetic-route"})
        state.collected_info["llm_governance"] = {"model": "forged", "prompt_version": "forged"}
        state.trace = [{"node": "synthetic", "input": {}, "output": {}}]
        async with factory() as db:
            await ChatService().persist_result(db, await db.get(Session, "session-101"), state,
                                               state.user_message, "synthetic-trace", commit=False)
            await db.commit()
        async with factory() as db:
            evaluation = await db.scalar(select(EvaluationResult))
            trace = await db.scalar(select(AgentTrace))
            assert evaluation.model_name == trace.model_name == "synthetic-primary"
            assert evaluation.prompt_version == trace.prompt_version == "synthetic-v1"


async def test_partial_service_usage_does_not_report_cost_with_missing_output_as_zero(monkeypatch):
    from time import perf_counter
    from app.services.llm_service import LLMService
    monkeypatch.setattr("app.services.llm_service.settings.LLM_GATEWAY_ENABLED", True)
    service = LLMService()
    service.last_call_metadata = {"provider_usage": {"input_tokens": 2}}
    try:
        await service._record_call_metadata(101, "synthetic", 3, perf_counter())
        assert service.last_call_metadata["cost_usd"] is None
        assert service.last_call_metadata["cost_basis"] == "partial_provider_usage_see_model_calls"
    finally:
        await service.close()
