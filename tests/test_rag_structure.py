"""隔离SQL和合成模型验证RAG结构优化，不访问真实模型/业务数据。"""
import hashlib
import json
import math
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.config.config import settings
from app.models.models import KnowledgeChunk, KnowledgeVersion
from app.services.knowledge_base.bm25 import BM25Scorer
from app.services.knowledge_base.chunking import source_chunks
from app.services.knowledge_base.evidence_assembly import assemble_evidence
from app.services.knowledge_base.ingestion import DocumentChunker
from app.services.knowledge_base.lifecycle import embedding_identity
from app.services.knowledge_base.retrieval_text import MAX_CONTEXT_CHARS, retrieval_text
from app.services.reranker_service import MAX_DOC_CHARS, RerankerService
from tests.test_m3_lifecycle import execute, kb as kb


@pytest.fixture(autouse=True)
def isolated_models(monkeypatch):
    monkeypatch.setattr(settings, "RAG_USE_RERANKER", False)
    monkeypatch.setattr(settings, "RAG_CONTEXT_NEIGHBORS", 1)
    monkeypatch.setattr(settings, "RERANKER_LOCAL_BASE", "")
    monkeypatch.setattr(settings, "RERANKER_API_BASE", "")


@pytest.mark.parametrize("pattern", ["heading", "sentence", "paragraph", "fixed"])
@pytest.mark.parametrize("overlap", [0, 17])
def test_full_source_coverage_positions_and_hard_limit(pattern, overlap):
    text = "概述与适用范围。\r\n# 产品\n## 安装\n" + "长句无标点🙂" * 90 + "\n### 限制\n注意：户外禁止使用。"
    chunks = source_chunks(text, 96, overlap, pattern)
    covered = set()
    for item in chunks:
        start, end = item["start_offset"], item["end_offset"]
        assert item["content"] == text[start:end]
        assert len(item["content"]) <= 96
        assert item["content_hash"] == hashlib.sha256(text[start:end].encode()).hexdigest()
        covered.update(range(start, end))
    assert covered == set(range(len(text)))


def test_heading_tree_preamble_and_fences():
    text = "适用范围：室内。\n# 产品A\n## 安装\n### 接口\n使用USB。\n```md\n## 假标题\n```\n## 保修\n期限一年。"
    chunks = source_chunks(text)
    assert "适用范围" in chunks[0]["content"]
    usb = next(c for c in chunks if "使用USB" in c["content"])
    assert usb["source_context"]["heading_path"] == ["产品A", "安装", "接口"]
    warranty = next(c for c in chunks if "期限一年" in c["content"])
    assert warranty["section"] == "产品A / 保修"
    assert len(chunks) == 3  # 父标题没有成为单独的空正文片段。
    assert len(source_chunks("\n\n# 标题\n## 章节\n正文。")) == 1


def test_long_faq_inherits_question_and_does_not_leak_to_next_question():
    text = "# 产品\n## 售后\n**Q: 能退吗？**\n" + "普通商品可申请。" * 30 + "定制商品除外。\nQ: 保修多久？\nA: 一年。"
    chunks = source_chunks(text, 96, 12)
    tail = next(c for c in chunks if "定制商品除外" in c["content"])
    assert "能退吗" in tail["source_context"]["faq_question"]
    warranty = next(c for c in chunks if "一年" in c["content"])
    assert "保修多久" in warranty["source_context"]["faq_question"]
    assert tail["source_context"]["unit_id"] != warranty["source_context"]["unit_id"]


def test_table_header_repeated_in_context_but_not_other_section():
    text = "## 参数\n| 型号 | 电压（V） |\n| --- | --- |\n" + "| A | 220 |\n" * 25 + "## 售后\n保修一年。"
    chunks = source_chunks(text, 96, 0)
    middle = next(c for c in chunks if c["start_offset"] > 100 and "A | 220" in c["content"])
    assert "电压（V）" in middle["source_context"]["table_header"]
    assert not chunks[-1]["source_context"]["table_header"]


def test_legacy_chunker_uses_same_policy_and_zero_overlap():
    text = "# 产品\n## 安装\n" + "连接设备。" * 30
    direct = source_chunks(text, 96, 0)
    legacy = DocumentChunker(96, 0, "heading").chunk_document(text, {"title": "设备"})
    assert [(c["content"], c["start_offset"], c["end_offset"]) for c in direct] == [
        (c["content"], c["start_offset"], c["end_offset"]) for c in legacy]


@pytest.mark.parametrize("size,overlap,pattern", [(63, 0, "heading"), (96, 96, "heading"),
                                                (96, -1, "heading"), (96, 0, "bad")])
def test_invalid_chunk_policy_rejected(size, overlap, pattern):
    with pytest.raises(ValueError):
        source_chunks("正文", size, overlap, pattern)


def test_context_text_is_bounded_without_cutting_raw_tail():
    item = {"title": "标题" * 2000, "content": "正文。例外不可遗漏。",
            "source_context": {"heading": "章节" * 1000}}
    text = retrieval_text(item)
    assert text.endswith(item["content"])
    assert len(text) <= MAX_CONTEXT_CHARS + len(item["content"])


def test_bm25_formula_boundaries_and_rebuild():
    scorer = BM25Scorer()
    scorer.build_index(["alpha", "beta"])
    assert scorer.score("alpha", 0) == pytest.approx(math.log(2))
    assert scorer.search("alpha", 5) == [(0, scorer.score("alpha", 0))]
    tokens = scorer._tokenize("退，货 型号ZX-900 错误E12")
    assert "退货" not in tokens and "货型" not in tokens
    assert "zx-900" in tokens and "e12" in tokens
    scorer.build_index([])
    assert not scorer.search("alpha", 5) and scorer.score("alpha", -1) == 0


def test_index_embedding_lexical_and_rerank_share_context(kb):
    original = "## 接口\n支持连接。"
    execute(kb, kb.add_document("ZX900投影仪", original, keywords=["HDMI"]))
    _, chunks = kb._repository.snapshot()
    expected = retrieval_text(chunks[0])
    assert kb._embedding_service.calls[0] == [expected]
    result = kb.search("ZX900", retrieval_mode="bm25", top_k=1)
    assert result["results"][0]["evidence_text"] == expected
    assert result["results"][0]["content"] == original
    svc = RerankerService(api_base="http://synthetic.invalid", model="synthetic")
    provider = Mock(return_value=[(0, .9)])
    svc._providers[0].rerank = provider
    assert svc.rerank_sync("接口", chunks, 1)
    assert provider.call_args.args[1] == [expected]


def test_production_worker_honors_configuration_and_freezes_retries(kb, monkeypatch):
    monkeypatch.setattr(settings, "CHUNK_SIZE", 96)
    monkeypatch.setattr(settings, "CHUNK_OVERLAP", 0)
    text = "# 合成设备\n## 说明\n" + "合成条款。" * 70
    result = kb.add_document("说明", text)
    kb._embedding_service.fail = True
    execute(kb, result)
    with kb._repository.transaction() as db:
        version = db.get(KnowledgeVersion, result["version_id"])
        assert version.embedding_metadata["index_plan"]["size"] == 96
    monkeypatch.setattr(settings, "CHUNK_SIZE", 256)
    monkeypatch.setattr(settings, "CHUNK_OVERLAP", 50)
    kb._embedding_service.fail = False
    execute(kb, result)
    _, chunks = kb._repository.snapshot()
    assert max(len(c["content"]) for c in chunks) <= 96
    assert [c["content"] for c in chunks] == [c["content"] for c in source_chunks(text, 96, 0)]


def test_snapshot_and_bm25_reused_for_different_queries(kb, monkeypatch):
    execute(kb, kb.add_document("设备", "USB连接，HDMI连接。"))
    snapshot = Mock(wraps=kb._repository.snapshot)
    epoch = Mock(wraps=kb._repository.current_epoch)
    monkeypatch.setattr(kb._repository, "snapshot", snapshot)
    monkeypatch.setattr(kb._repository, "current_epoch", epoch)
    kb.search("USB", retrieval_mode="bm25")
    other = kb.search("HDMI", retrieval_mode="bm25")
    assert not other["from_cache"] and other["meta"]["lexical_index_cache"] == "hit"
    assert snapshot.call_count == 1 and epoch.call_count == 2
    assert kb._retrieval_runtime.lexical_builds == 1
    hot = kb.search("HDMI", retrieval_mode="bm25")
    assert hot["from_cache"] and epoch.call_count == 3
    assert all(value == 0 for value in hot["meta"]["timings_ms"].values() if value != hot["latency_ms"])


def test_unpublish_invalidates_cached_snapshot_and_evidence(kb):
    result = kb.add_document("设备", "USB连接。")
    execute(kb, result)
    old = kb.search("USB", retrieval_mode="bm25")
    kb.set_document_published(result["document_id"], False)
    assert not kb.search("USB", retrieval_mode="bm25")["results"]
    assert not kb.evidence_is_current(old["results"])


def test_old_text_fingerprint_does_not_enter_dense_route(kb):
    result = kb.add_document("设备", "USB连接。")
    execute(kb, result)
    _, identity = embedding_identity(kb._embedding_service)
    identity.pop("document_text_policy")
    legacy = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    with kb._repository.transaction() as db:
        db.get(KnowledgeVersion, result["version_id"]).embedding_fingerprint = legacy
        kb._repository.bump(db)
    found = kb.search("USB")
    assert found["results"] and not found["meta"]["routes"]["dense"]["ids"]
    assert "embedding_fingerprint_mismatch" in found["degraded_reason"]


def item(identity, body, *, unit="0:200", start=0, version="v1", document="doc"):
    return {"id": identity, "document_id": document, "version_id": version,
            "content": body, "start_offset": start, "end_offset": start + len(body),
            "source_context": {"unit_id": unit, "heading": "售后"}, "final_score": .03}


def test_neighbors_keep_identity_and_do_not_cross_faq_or_version():
    seed = item("a", "支持申请。")
    tail = item("b", "定制商品除外。", start=8)
    wrong = item("c", "另一个问题。", unit="200:300", start=20)
    stale = item("d", "旧版条款。", version="v0", start=9)
    results, meta = assemble_evidence([seed], [seed, tail, wrong, stale], top_k=3, max_chars=512, neighbors=1)
    assert [c["id"] for c in results] == ["a", "b"]
    assert results[1]["content"] == tail["content"] and results[1]["start_offset"] == 8
    assert meta["neighbor_count"] == 1


def test_dedup_is_exact_and_scope_aware_budget_never_truncates():
    rows = [item("a", "普通商品可退。"), item("b", "普通商品可退。"),
            item("c", "定制商品不可退。"), item("d", "普通商品可退。", document="other")]
    results, meta = assemble_evidence(rows, rows, top_k=3, max_chars=512, neighbors=0)
    assert [c["id"] for c in results] == ["a", "c", "d"]
    assert meta["duplicates_removed"] == 1
    long = item("long", "说明" * 300 + "例外：不可退。")
    results, meta = assemble_evidence([long], [long], top_k=1, max_chars=512, neighbors=0)
    assert results == [] and meta["over_budget_count"] == 1


def test_reranker_overlong_input_preserves_tail_instead_of_silent_truncation():
    svc = RerankerService(api_base="http://synthetic.invalid", model="synthetic")
    provider = Mock(side_effect=AssertionError("must not dispatch truncated evidence"))
    svc._providers[0].rerank = provider
    assert svc.rerank_sync("例外", [{"content": "长" * MAX_DOC_CHARS + "不可退"}], 1) is None
    provider.assert_not_called()


def test_result_mutation_cannot_corrupt_runtime_snapshot(kb):
    execute(kb, kb.add_document("设备", "USB连接。"))
    first = kb.search("USB", retrieval_mode="bm25")
    first["results"][0]["source_context"]["heading"] = "伪造章节"
    first["results"][0]["content"] = "伪造正文"
    second = kb.search("USB连接", retrieval_mode="bm25")
    assert second["results"][0]["content"] == "USB连接。"
    assert second["results"][0]["source_context"]["heading"] != "伪造章节"


def test_stored_chunk_text_remains_exact_source(kb):
    original = "概述。\n# 产品\n## 接口\n支持USB。\n## 限制\n不支持户外。"
    execute(kb, kb.add_document("设备", original))
    with kb._repository.transaction() as db:
        for chunk in db.scalars(select(KnowledgeChunk)):
            assert chunk.content == original[chunk.start_offset:chunk.end_offset]


def test_concurrent_queries_share_one_snapshot_and_lexical_index(kb):
    from concurrent.futures import ThreadPoolExecutor
    execute(kb, kb.add_document("设备", "USB HDMI 接口连接。"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: kb.search(f"USB {i}", retrieval_mode="bm25"), range(16)))
    assert all(result["results"] for result in results)
    assert kb._retrieval_runtime.snapshot_builds == kb._retrieval_runtime.lexical_builds == 1


def test_late_old_request_cannot_replace_new_epoch_index(kb):
    first = kb.add_document("设备", "USB连接。")
    execute(kb, first)
    runtime = kb._retrieval_runtime
    old_epoch, old_chunks, _ = runtime.snapshot(kb._repository)
    updated = kb.update_document(first["document_id"], content="HDMI连接。", expected_version=1)
    execute(kb, updated)
    new_epoch, new_chunks, _ = runtime.snapshot(kb._repository)
    current_index, _ = runtime.lexical(new_epoch, new_chunks)
    runtime.lexical(old_epoch, old_chunks)
    assert runtime.lexical(new_epoch, new_chunks) == (current_index, True)
    assert current_index.search("HDMI", 3) and not current_index.search("USB", 3)


def test_parameter_change_does_not_reuse_assembled_result_cache(kb, monkeypatch):
    execute(kb, kb.add_document("设备", "USB连接。"))
    assert not kb.search("USB", retrieval_mode="bm25")["from_cache"]
    assert kb.search("USB", retrieval_mode="bm25")["from_cache"]
    monkeypatch.setattr(settings, "RAG_RRF_K", 80)
    changed = kb.search("USB", retrieval_mode="bm25")
    assert not changed["from_cache"] and changed["results"][0]["final_score"] == pytest.approx(1 / 81)
    monkeypatch.setattr(settings, "RAG_CONTEXT_NEIGHBORS", 0)
    assert not kb.search("USB", retrieval_mode="bm25")["from_cache"]


def test_synthetic_diagnostic_uses_production_pipeline_and_restores_settings():
    from scripts.evaluate_rag_structure import evaluate
    before = settings.CHUNK_SIZE
    report = evaluate()
    assert report["real_model_calls"] == 0 and report["whole_m5_pass"] is False
    assert report["document_hit_at_3"] == report["required_source_text_present"] == 1
    assert report["snapshot_builds"] == report["lexical_index_builds"] == 1
    assert all(row["cache_origin_correct"] for row in report["rows"])
    assert settings.CHUNK_SIZE == before
