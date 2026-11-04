from unittest.mock import Mock
import pytest
from tests.test_m3_lifecycle import kb as kb,execute


def test_bm25_mode_does_not_call_embedding_and_isolated_from_hybrid_cache(kb,monkeypatch):
    execute(kb,kb.add_document("合成条件","合成商品签收后七天，定制商品除外"))
    hybrid=kb.search("定制商品",retrieval_mode="hybrid")
    assert hybrid["meta"]["routes"]["dense"]["ids"]
    encode=Mock(side_effect=AssertionError("BM25 ablation must not encode"))
    monkeypatch.setattr(kb._embedding_service,"encode_query",encode)
    lexical=kb.search("定制商品",retrieval_mode="bm25")
    assert lexical["results"] and not lexical["from_cache"]
    assert lexical["meta"]["routes"]["dense"]["ids"]==[]
    assert lexical["meta"]["retrieval_mode"]=="bm25"
    assert not lexical["degraded"] and lexical["degraded_reason"] is None
    encode.assert_not_called()


def test_dense_mode_does_not_execute_bm25_and_hot_cache_reports_zero_provider_work(kb,monkeypatch):
    execute(kb,kb.add_document("合成条件","合成商品签收后七天，定制商品除外"))
    lexical=Mock(side_effect=AssertionError("dense ablation must not score lexical"))
    monkeypatch.setattr("app.services.knowledge_base.versioned_search.BM25Scorer.score",lexical)
    dense=kb.search("定制商品",retrieval_mode="dense")
    assert dense["results"] and dense["meta"]["routes"]["bm25"]["ids"]==[]
    cached=kb.search("定制商品",retrieval_mode="dense")
    assert cached["from_cache"] and cached["meta"]["timings_ms"]["encode"]==0
    lexical.assert_not_called()


def test_unregistered_mode_rejected_before_initialization(kb):
    with pytest.raises(ValueError,match="invalid_retrieval_mode"):
        kb.search("合成",retrieval_mode="anything")
