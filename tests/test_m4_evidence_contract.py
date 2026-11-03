from copy import deepcopy
from unittest.mock import AsyncMock
import pytest
from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.agent.request_understanding import EvidenceBundle
from app.agent.nodes.rag_node import rag_retrieval_node
from app.agent.nodes.response_node import validate_candidate, response_generation_node
from tests.test_chat_turns import graph_for_response


def result():
    chunks = [{"id": "chunk1", "version_id": "version1", "version": 1,
               "title": "合成政策", "content": "合成商品保修规则", "final_score": .8}]
    return {"success": True, "results": chunks, "meta": {"score_type": "rrf", "knowledge_epoch": 1},
            "evidence_bundle": {"schema_version": 1, "knowledge_epoch": 1,
                                "items": deepcopy(chunks), "support": "unknown"}}


@pytest.mark.parametrize("invalid", ["failed", "mismatched", "schema", "epoch"])
async def test_failed_or_inconsistent_retrieval_cannot_supply_answer_evidence(monkeypatch, invalid):
    value = result()
    if invalid == "failed":
        value["success"] = False
    elif invalid == "mismatched":
        value["evidence_bundle"]["items"][0]["content"] = "另一版冲突规则"
    elif invalid == "epoch":
        value["evidence_bundle"]["knowledge_epoch"] = 2
    else:
        value["evidence_bundle"]["schema_version"] = 999
    kb = type("KB", (), {"async_search": AsyncMock(return_value=value)})()
    monkeypatch.setattr("app.agent.graph.get_knowledge_base", lambda: kb)
    graph = AgentGraph()
    try:
        state = AgentState(session_id="s", user_id=101, user_message="合成问题")
        await rag_retrieval_node(graph, state)
        assert state.collected_info["rag_grounded"] is False
        assert state.collected_info["kb_results"] == []
        assert state.evidence_bundle.items == []
        assert not state.tool_calls[-1].success
        assert state.tool_calls[-1].result["results"] == []
    finally:
        await graph.llm_service.close()


async def test_invalidated_evidence_clears_typed_legacy_and_prior_review(monkeypatch):
    from app.agent.request_understanding import ReviewResult

    graph = graph_for_response()
    state = AgentState(session_id="s", user_id=101, user_message="合成问题", reply="合成回答",
                       evidence_bundle=EvidenceBundle.model_validate(result()["evidence_bundle"]),
                       review_result=ReviewResult(passed=True, evidence_version="prior"),
                       collected_info={"kb_results": result()["results"], "rag_grounded": True})
    stale = type("KB", (), {"evidence_is_current": lambda *args: False})()
    monkeypatch.setattr("app.services.knowledge_base.get_knowledge_base", lambda: stale)
    await validate_candidate(graph, state)
    assert state.evidence_bundle.items == []
    assert state.evidence_bundle.support == "unsupported"
    assert state.collected_info["kb_results"] == []


async def test_review_is_bound_to_evidence_snapshot_and_next_turn_has_no_evidence(monkeypatch):
    graph = graph_for_response()
    state = AgentState(session_id="s", user_id=101, user_message="合成问题", reply="合成回答",
                       evidence_bundle=EvidenceBundle.model_validate(result()["evidence_bundle"]),
                       collected_info={"kb_results": result()["results"], "rag_grounded": True})
    current = type("KB", (), {"evidence_is_current": lambda *args: True})()
    monkeypatch.setattr("app.services.knowledge_base.get_knowledge_base", lambda: current)
    await validate_candidate(graph, state)
    assert state.review_result.evidence_version
    fingerprint = state.review_result.evidence_version
    state.evidence_bundle.items[0]["version_id"] = "version2"
    state.evidence_bundle.items[0]["content"] = "变更后的规则"
    await validate_candidate(graph, state)
    assert state.review_result.evidence_version != fingerprint
    fresh = state.for_next_turn(101, "新问题")
    assert fresh.evidence_bundle is None and fresh.review_result is None


def test_typed_evidence_is_independent_of_provider_and_legacy_projection():
    state = AgentState(session_id="s", user_id=101, user_message="合成问题")
    supplied = result()
    assert state.accept_retrieval(supplied)
    supplied["results"][0]["content"] = "供应商对象随后被修改"
    supplied["evidence_bundle"]["items"][0]["content"] = "供应商bundle被修改"
    state.collected_info["kb_results"][0]["content"] = "旧字典投影被修改"
    assert state.current_evidence()[0]["content"] == "合成商品保修规则"
    exposed = state.current_evidence()
    exposed[0]["content"] = "读取者随后修改"
    assert state.current_evidence()[0]["content"] == "合成商品保修规则"


def test_cache_key_includes_typed_snapshot_epoch_and_version_identity():
    graph = graph_for_response()
    state = AgentState(session_id="s", user_id=101, user_message="合成问题")
    assert state.accept_retrieval(result())
    first = graph._build_answer_cache_key(state)
    state.evidence_bundle.knowledge_epoch = 2
    second = graph._build_answer_cache_key(state)
    assert second != first
    state.evidence_bundle.items[0]["version_id"] = "different-version-id"
    assert graph._build_answer_cache_key(state) != second


def test_provider_cannot_self_attest_answer_support():
    value = result()
    value["evidence_bundle"]["support"] = "supported"
    state = AgentState(session_id="s", user_id=101, user_message="合成问题")
    assert state.accept_retrieval(value)
    assert state.evidence_bundle.support == "unknown"


@pytest.mark.parametrize("metadata", [[1, 2], "not-an-object"])
def test_malformed_retrieval_metadata_fails_closed(metadata):
    value = {**result(), "meta": metadata}
    state = AgentState(session_id="s", user_id=101, user_message="合成问题")
    assert not state.accept_retrieval(value)
    assert state.evidence_bundle.items == [] and state.retrieval_error == "retrieval_contract_invalid"


async def test_version_change_during_generation_cannot_cache_invalidation_notice(monkeypatch):
    graph = graph_for_response()
    state = AgentState(session_id="s", user_id=101, user_message="合成问题",
                       evidence_bundle=EvidenceBundle.model_validate(result()["evidence_bundle"]),
                       collected_info={"kb_results": result()["results"], "rag_grounded": True})
    checks = []

    def current(*args):
        checks.append(True)
        return len(checks) == 1

    kb = type("KB", (), {"evidence_is_current": staticmethod(current)})()
    monkeypatch.setattr("app.services.knowledge_base.get_knowledge_base", lambda: kb)
    monkeypatch.setattr("app.agent.nodes.response_node.settings.USER_MEMORY_ENABLED", False)
    await response_generation_node(graph, state)
    assert "知识版本已变化" in state.reply
    assert graph._answer_cache.values == {}
    assert not state.current_evidence()
