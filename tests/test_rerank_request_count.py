from unittest.mock import Mock

from app.config.config import settings
from app.services.reranker_service import RerankerService


def test_full_candidate_batch_dispatches_once_without_losing_last_candidate(monkeypatch):
    monkeypatch.setattr(settings, "RERANKER_LOCAL_BATCH_SIZE", 20)
    service = RerankerService(local_base="http://127.0.0.1:7997", local_model="BAAI/bge-reranker-large")
    provider = service._providers[0]
    call = Mock(side_effect=lambda query, docs, top_n: sorted(
        [(i, float(doc)) for i, doc in enumerate(docs)], key=lambda row: -row[1]))
    monkeypatch.setattr(provider, "rerank", call)
    try:
        ranked = service.rerank_sync("synthetic", [{"id": str(i), "content": str(i)} for i in range(20)], 5)
        assert [r["id"] for r in ranked] == ["19", "18", "17", "16", "15"]
        assert call.call_count == 1
        assert len(call.call_args.args[1]) == 20
    finally:
        service.close()
