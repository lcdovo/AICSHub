"""Enabled model/task/item-v2 publication path; all model outputs are explicit substitutes."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config.config import Settings, settings
from app.agent.answer_contracts import AnswerDraft
from app.agent.answer_review import validate_candidate
from app.agent.answer_publication import publish_answer
from app.agent.validation import ValidationResult
from app.agent.validation_llm import LLMRAGJudge
from app.agent.intent_items import snapshot
from app.agent.answer_contracts import tool_evidence_text
from app.agent.item_judge_v2 import to_fragment_verdict, to_sentence_verdict
from app.agent.item_judge_selectors import fixture_selectors
from scripts.offline_profiles import ACTIVE_CONFIG
from tests.test_item_judge_v2 import fixture


@pytest.fixture(autouse=True)
def enabled_features(monkeypatch):
    parsed = Settings(_env_file=None, **ACTIVE_CONFIG)
    for key in ACTIVE_CONFIG:
        monkeypatch.setattr(settings, key, getattr(parsed, key))
    monkeypatch.setattr(settings, "USER_MEMORY_ENABLED", False)
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", False)


@pytest.mark.parametrize("fault", ["none", "timeout", "missing_verdict", "stale_evidence"])
async def test_enabled_item_v2_publication_succeeds_or_fails_closed(fault):
    assert settings.REQUEST_UNDERSTANDING_MODE == "model"
    assert settings.AGENT_TASK_LOOP_ENABLED and settings.AGENT_ITEM_COMPLETION_ENABLED
    assert settings.LLM_GATEWAY_ENABLED and settings.LLM_JUDGE_ENABLED
    assert settings.LLM_JUDGE_CONTRACT_VERSION == "v2"
    assert settings.LLM_JUDGE_V2_OUTPUT_PROTOCOL == "json_object"
    state, body, verdict = fixture("MI-01")
    state.reply = body
    value = to_sentence_verdict(to_fragment_verdict(verdict, tool_evidence_text(state),
                                                   snapshot(state), state.current_evidence()))
    value = fixture_selectors(value, body, snapshot(state), tool_evidence_text(state), state.current_evidence())
    call = AsyncMock(return_value=value)
    if fault == "timeout":
        call.side_effect = asyncio.TimeoutError("synthetic judge timeout")
    elif fault == "missing_verdict":
        call.return_value = None
    model = SimpleNamespace(is_available=lambda: True, run_structured=call)
    context = SimpleNamespace(
        knowledge_factory=lambda: SimpleNamespace(evidence_is_current=lambda items: fault != "stale_evidence"),
        validator=SimpleNamespace(validate=lambda **kwargs: ValidationResult()),
        llm_judge=LLMRAGJudge(model), _add_rag_citations=lambda text, items, **kwargs: text,
    )
    accepted = await validate_candidate(context, state)
    if fault == "none":
        assert accepted
        await publish_answer(context, state, AnswerDraft())
        assert state.review_result.passed and state.task.status == "completed"
        assert all(status == "answered" for status in state.task.item_outcomes.values())
        call.assert_awaited_once()
    elif fault == "stale_evidence":
        await publish_answer(context, state, AnswerDraft())
        assert state.reply != body
        assert not state.current_evidence()
        assert state.task.status != "completed"
    else:
        assert not accepted
        assert not state.review_result.passed
        assert state.task.status != "completed"


@pytest.mark.parametrize("fault", ["none", "timeout", "missing", "wrong_schema"])
async def test_enabled_understanding_uses_item_schema_once_and_never_legacy(fault):
    from app.agent.request_understanding import understand
    from app.agent.state import AgentState
    from app.agent.request_understanding import ItemizedTaskUnderstandingWire
    from tests.understanding_wire_fixture import item_wire
    prepared, _, _ = fixture("MI-01")
    fresh = AgentState(session_id="active-understanding", user_id=101, actor_id=101,
                       source_message_id=17, user_message=prepared.user_message,
                       understanding_mode="model", task_loop_enabled=True)
    value = item_wire(prepared.understanding.model_dump())
    if fault == "missing":
        value = None
    elif fault == "wrong_schema":
        value = {"topic": "refund", "request_type": "apply"}
    call = AsyncMock(return_value=value)
    if fault == "timeout":
        call.side_effect = asyncio.TimeoutError("synthetic understanding timeout")
    model = SimpleNamespace(run_structured=call, is_available=lambda: True)
    parsed = await understand(model, fresh)
    assert (parsed is not None) is (fault == "none")
    assert call.await_args.args[2] is ItemizedTaskUnderstandingWire
    assert call.await_args.kwargs["single_attempt"] is True
    call.assert_awaited_once()
    assert not fresh.tool_calls and not fresh.pending_confirmations
