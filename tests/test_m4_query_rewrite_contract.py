"""查询改写观察属于当前轮次；兼容投影不替代已验证结论。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.nodes.rag_node import QueryRewriteDependencies, rewrite_query, RetrievalDependencies, retrieve_evidence
from app.agent.state import AgentState
from tests.test_m4_understanding import result as understanding


def state():
    return AgentState(session_id="synthetic", user_id=101, user_message="它的保修呢？", source_message_id=7)


def rewrite_result(**changes):
    values = dict(original_query="它的保修呢？", rewritten_query="耳机保修期限", applied=True,
                  reason="synthetic", confidence=.9, simple=False)
    return SimpleNamespace(**{**values, **changes})


async def test_unified_understanding_records_applied_and_typed_observation():
    value = state()
    value.understanding = understanding(retrieval_query="耳机保修期限")
    rewrite = AsyncMock(side_effect=AssertionError("unified query must not call another model"))
    await rewrite_query(QueryRewriteDependencies(SimpleNamespace(rewrite=rewrite)), value)
    assert value.query_rewrite_applied
    assert value.query_rewrite.origin == "unified_understanding"
    assert value.collected_info["query_rewrite"]["rewritten_query"] == "耳机保修期限"
    rewrite.assert_not_awaited()


@pytest.mark.parametrize("changes", [
    {"original_query": "别的轮次"}, {"rewritten_query": []}, {"applied": "true"},
    {"confidence": float("nan")}, {"confidence": 1.1}, {"simple": 1},
    {"applied": False}, {"rewritten_query": "字" * 301},
])
async def test_invalid_rewrite_returns_original_query_and_marks_error(changes):
    value = state()
    rewrite = AsyncMock(return_value=rewrite_result(**changes))
    await rewrite_query(QueryRewriteDependencies(SimpleNamespace(rewrite=rewrite)), value)
    assert value.rewritten_query == value.user_message
    assert not value.query_rewrite_applied
    assert value.query_rewrite_error == "query_rewrite_invalid"
    assert value.query_rewrite.origin == "fallback"


async def test_projection_changes_cannot_replace_current_query_or_cache_identity():
    from app.agent.nodes.response_node import build_answer_cache_key
    value = state()
    rewrite = AsyncMock(return_value=rewrite_result())
    await rewrite_query(QueryRewriteDependencies(SimpleNamespace(rewrite=rewrite)), value)
    context = SimpleNamespace(_build_llm_prompt=lambda _: "synthetic-prompt")
    cache_key = build_answer_cache_key(context, value)
    value.rewritten_query = "伪造投影"
    value.collected_info["query_rewrite"] = {"rewritten_query": "另一个投影"}
    assert build_answer_cache_key(context, value) == cache_key
    search = AsyncMock(return_value={"success": True, "results": [], "meta": {}})
    await retrieve_evidence(RetrievalDependencies(lambda: SimpleNamespace(async_search=search), lambda: 3, .35), value)
    search.assert_awaited_once_with(query="耳机保修期限", top_k=3)


@pytest.mark.parametrize("change", ["user", "session", "message", "source", "version"])
async def test_restored_rewrite_invalidated_when_turn_context_changes(change):
    value = state()
    rewrite = AsyncMock(return_value=rewrite_result())
    await rewrite_query(QueryRewriteDependencies(SimpleNamespace(rewrite=rewrite)), value)
    restored = AgentState.model_validate_json(value.model_dump_json())
    if change == "user": restored.user_id = 202
    elif change == "session": restored.session_id = "other"
    elif change == "message": restored.user_message = "新问题"
    elif change == "source": restored.source_message_id = 8
    else: restored.session_version = 2
    assert restored.current_retrieval_query() == restored.user_message
    assert restored.query_rewrite_error == "query_rewrite_context_changed"
    assert not restored.query_rewrite_applied


async def test_next_turn_clears_observation_and_keeps_previous_state_unchanged():
    value = state()
    rewrite = AsyncMock(return_value=rewrite_result())
    await rewrite_query(QueryRewriteDependencies(SimpleNamespace(rewrite=rewrite)), value)
    before = value.model_dump()
    fresh = value.for_next_turn(101, "新问题")
    assert fresh.query_rewrite is None and fresh.query_rewrite_error is None
    assert "query_rewrite" not in fresh.collected_info
    assert fresh.current_retrieval_query() == "新问题"
    assert value.model_dump() == before


def test_observation_rejects_extra_scope_and_remains_frozen():
    value = state()
    raw = vars(rewrite_result())
    assert not value.record_query_rewrite({**raw, "user_id": 202}, "rewriter")
    assert value.current_retrieval_query() == value.user_message
    assert value.record_query_rewrite(raw, "rewriter")
    with pytest.raises(ValueError):
        value.query_rewrite.rewritten_query = "changed"


def test_valid_legacy_fields_import_once_but_legacy_dictionary_is_not_a_source():
    value = state()
    value.rewritten_query = "耳机保修期限"
    assert value.current_retrieval_query() == "耳机保修期限"
    assert value.query_rewrite.origin == "legacy"
    value.rewritten_query = "后改平铺字段"
    assert value.current_retrieval_query() == "耳机保修期限"
    untouched = state()
    untouched.collected_info["query_rewrite"] = {"rewritten_query": "旧字典"}
    assert untouched.current_retrieval_query() == untouched.user_message


async def test_unchanged_unified_query_is_not_marked_as_applied():
    value = state()
    value.understanding = understanding(retrieval_query=value.user_message)
    await rewrite_query(QueryRewriteDependencies(SimpleNamespace(rewrite=AsyncMock())), value)
    assert not value.query_rewrite_applied
    assert value.current_retrieval_query() == value.user_message
