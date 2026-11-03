from types import SimpleNamespace
import pytest
from app.config.config import settings
from app.services.knowledge_base import KnowledgeBaseService
from tests.test_m3_lifecycle import kb, execute


def test_embedding_failure_retains_bm25_and_truthful_metadata(kb, monkeypatch):
    item = kb.add_document("说明", "ZX900 精确型号维修条款")
    execute(kb, item)
    monkeypatch.setattr(
        kb._embedding_service,
        "encode_query",
        lambda q: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    result = kb.search("ZX900", top_k=1)
    assert (
        result["results"] and result["search_strategy"] == "bm25" and result["degraded"]
    )
    assert (
        result["meta"]["routes"]["dense"]["ids"] == []
        and result["meta"]["routes"]["bm25"]["ids"]
    )
    assert (
        result["execution_time_ms"]
        == result["latency_ms"]
        == result["meta"]["latency_ms"]
    )


def test_cached_workers_refresh_on_publish_delete_and_fingerprint_change(
    kb, monkeypatch
):
    first = kb.add_document("政策", "ZX900 旧政策")
    execute(kb, first)
    second = KnowledgeBaseService(
        repository=kb._repository, embedding=kb._embedding_service
    )
    for service in (kb, second):
        assert service.search("ZX900")["results"][0]["version"] == 1
        assert service.search("ZX900")["from_cache"]
    changed = kb.update_document(
        first["document_id"], content="ZX900 新政策", expected_version=1
    )
    execute(kb, changed)
    for service in (kb, second):
        result = service.search("ZX900")
        assert not result["from_cache"] and {
            c["version"] for c in result["results"]
        } == {2}
    monkeypatch.setattr(settings, "EMBEDDING_REVISION", "new-same-dim")
    changed_space = second.search("ZX900")
    assert (
        not changed_space["from_cache"]
        and changed_space["meta"]["routes"]["dense"]["ids"] == []
    )
    assert "fingerprint_mismatch" in changed_space["degraded_reason"]
    kb.delete_document(first["document_id"])
    assert not kb.search("ZX900")["results"] and not second.search("ZX900")["results"]


def test_drafts_unpublished_versions_never_enter_cache_or_evidence(kb):
    draft = kb.add_document("草稿", "private-draft-marker", is_published=False)
    execute(kb, draft)
    assert kb.search("private-draft-marker")["results"] == []
    assert kb.set_document_published(draft["document_id"], True)
    old = kb.search("private-draft-marker")["results"]
    assert old
    assert kb.set_document_published(draft["document_id"], False)
    assert not kb.evidence_is_current(old)
    assert kb.search("private-draft-marker")["results"] == []


def test_rrf_not_compared_with_cosine_threshold_and_no_prefix_dedup(kb, monkeypatch):
    prefix = "共享条件" * 30
    for text in [prefix + "；定制品不支持退货。", prefix + "；普通品支持退货。"]:
        execute(kb, kb.add_document("同名", text))
    monkeypatch.setattr(settings, "RAG_USE_RERANKER", False)
    result = kb.search("退货", top_k=2)
    assert len(result["results"]) == 2 and all(
        c["score_type"] == "rrf" and c["final_score"] < 0.35 for c in result["results"]
    )
    assert len({c["content_hash"] for c in result["results"]}) == 2
    assert result["evidence_bundle"]["support"] == "unknown"


def test_independent_candidate_limits_and_rank_preserving_reranker_failure(
    kb, monkeypatch
):
    for i in range(20):
        execute(kb, kb.add_document(str(i), f"同一个政策关键词 唯一{i}"))

    class Reranker:
        last_used_provider = "synthetic-provider"

        def is_available(self):
            return True

        def rerank_sync(self, q, c, k):
            self.seen = len(c)
            self.input_ids = [item["id"] for item in c]
            return list(reversed(c))[:k]

    reranker = Reranker()
    kb._reranker = reranker
    monkeypatch.setattr(settings, "RAG_USE_RERANKER", True)
    result = kb.search("政策", top_k=3, recall_k=20, rerank_k=20)
    assert reranker.seen == 20 and len(result["results"]) == 3
    assert result["results"][0]["id"] == reranker.input_ids[19]
    assert result["meta"]["recall_k"] == 20 and result["meta"]["rerank_k"] == 20
    monkeypatch.setattr(settings, "RAG_USE_RERANKER", False)
    result = kb.search("政策", top_k=3, recall_k=20, rerank_k=10)
    assert len(result["results"]) == 3 and result["reranker"] == "none"


def test_embedding_timeout_and_milvus_failure_do_not_hide_keyword_route(
    kb, monkeypatch
):
    import threading
    from app.services.knowledge_base import versioned_search

    execute(kb, kb.add_document("关键词", "EXACT keyword policy"))
    release = threading.Event()
    monkeypatch.setattr(versioned_search, "ENCODE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        kb._embedding_service, "encode_query", lambda q: (release.wait(5), [1, 0, 0])[1]
    )
    try:
        result = kb.search("EXACT")
        assert result["search_strategy"] == "bm25" and result["degraded"]
    finally:
        release.set()


def test_milvus_timeout_fallback_metadata_and_model_pool_validation(kb, monkeypatch):
    execute(kb, kb.add_document("关键词", "EXACT keyword policy"))

    class BrokenMilvus:
        def search(self, *args):
            raise TimeoutError("synthetic unavailable")

    kb._vector_store = BrokenMilvus()
    result = kb.search("EXACT")
    assert result["meta"]["routes"]["dense"]["backend"] == "sql_vectors"
    assert result["degraded"] and "milvus_unavailable" in result["degraded_reason"]

    class InvalidReranker:
        last_used_provider = "synthetic"

        def is_available(self):
            return True

        def rerank_sync(self, *a):
            return [{"id": "not-in-candidates", "content": "injected"}]

    kb._reranker = InvalidReranker()
    monkeypatch.setattr(settings, "RAG_USE_RERANKER", True)
    changed = kb.search("EXACT", recall_k=10)
    assert changed["reranker"] == "rank_preserved" and all(
        c["id"] != "not-in-candidates" for c in changed["results"]
    )


async def test_rrf_gate_and_generation_recheck_do_not_use_stale_evidence(
    kb, monkeypatch
):
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.agent.nodes.rag_node import rag_retrieval_node
    from app.agent.nodes.response_node import validate_candidate
    from app.agent.validation import create_validator

    result = kb.add_document("规则", "ZXKEY 条款")
    execute(kb, result)
    monkeypatch.setattr("app.agent.graph.get_knowledge_base", lambda: kb)
    state = AgentState(session_id="synthetic", user_id=101, user_message="ZXKEY")
    await rag_retrieval_node(object.__new__(AgentGraph), state)
    assert (
        state.collected_info["rag_grounded"]
        and state.collected_info["best_match_score"] < 0.35
    )
    state.reply = "ZXKEY 旧证据答案"
    kb.delete_document(result["document_id"])
    monkeypatch.setattr("app.services.knowledge_base.get_knowledge_base", lambda: kb)
    graph = SimpleNamespace(
        validator=create_validator(), _rag_judge_applicable=lambda s: False
    )
    await validate_candidate(graph, state)
    assert (
        state.collected_info["rag_grounded"] is False
        and state.collected_info["kb_results"] == []
    )
    assert "知识版本已变化" in state.reply and "旧证据答案" not in state.reply
