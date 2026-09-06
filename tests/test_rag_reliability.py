"""第 5 步：RAG 重启恢复、生命周期和可复现评测。"""
from pathlib import Path

from app.config.config import settings
from app.services.knowledge_base import KnowledgeBaseService
from scripts.rag_benchmark import run_benchmark


def _new_kb(monkeypatch, tmp_path: Path) -> KnowledgeBaseService:
    monkeypatch.setattr(settings, "DATABASE_URL_OVERRIDE", f"sqlite+aiosqlite:///{tmp_path / 'rag.db'}")
    return KnowledgeBaseService()


def test_published_chunks_survive_restart_and_rebuild(monkeypatch, tmp_path):
    kb = _new_kb(monkeypatch, tmp_path)
    created = kb.add_document("退款说明", "银行卡退款通常需要3-7个工作日到账。", category="policy", keywords=["退款"])
    assert created["success"] is True
    assert kb.search("银行卡退款", top_k=1, similarity_threshold=0)["results"]

    # 新实例模拟进程重启，必须从 SQLite 恢复 content/chunks/embedding。
    restarted = _new_kb(monkeypatch, tmp_path)
    result = restarted.search("银行卡退款", top_k=1, similarity_threshold=0)
    assert result["results"][0]["content"] == "银行卡退款通常需要3-7个工作日到账。"
    assert result["backend"] == "memory"
    assert result["degraded"] is True
    rebuilt = restarted.rebuild_index()
    assert rebuilt["chunks"] == 1


def test_document_version_publish_and_delete_remove_old_content(monkeypatch, tmp_path):
    kb = _new_kb(monkeypatch, tmp_path)
    created = kb.add_document("物流政策", "旧物流政策：预计2天送达。")
    doc_id = created["document_id"]
    updated = kb.update_document(doc_id, content="新物流政策：预计1天送达。")
    assert updated["success"] is True
    assert updated["version"] == 2
    assert not any("旧物流" in item["content"] for item in kb.search("旧物流政策", similarity_threshold=0)["results"])
    new_id = updated["document_id"]
    assert kb.set_document_published(new_id, False) is True
    assert kb.search("新物流政策", similarity_threshold=0)["results"] == []
    assert kb.delete_document(new_id) is True
    restarted = _new_kb(monkeypatch, tmp_path)
    assert restarted.list_documents() == []


def test_benchmark_reports_quality_and_latency(monkeypatch, tmp_path):
    kb = _new_kb(monkeypatch, tmp_path)
    kb.add_document("退换货政策", "签收后7天内可无理由退货。")
    report = run_benchmark(kb, [{"query": "几天可以退货", "relevant_titles": ["退换货政策"]}, {"query": "火星旅行", "relevant_titles": [], "similarity_threshold": 1.0}], top_k=1)
    assert report["dataset_size"] == 2
    assert 0 <= report["recall_at_k"] <= 1
    assert 0 <= report["top1_accuracy"] <= 1
    assert "p95" in report["latency_ms"]
    assert report["empty_recall_rate"] == 1


def test_document_list_refreshes_after_external_writer(monkeypatch, tmp_path):
    first = _new_kb(monkeypatch, tmp_path)
    first.add_document("第一篇", "第一篇内容")
    second = _new_kb(monkeypatch, tmp_path)
    second.add_document("第二篇", "第二篇内容")

    # first 实例已初始化过，但列表读取应能看到另一个进程写入的文档。
    assert {item["title"] for item in first.list_documents()} == {"第一篇", "第二篇"}


def test_promotional_gift_query_expands_bm25_terms():
    expanded = KnowledgeBaseService._expand_bm25_query("买电子产品送什么赠品？")

    assert "赠品" in expanded
    assert "优惠" in expanded
    assert "促销" in expanded
    assert KnowledgeBaseService._expand_bm25_query("耳机如何连接") == "耳机如何连接"
