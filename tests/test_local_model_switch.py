"""隔离SQLite验证多文档发布事务，索引与Embedding使用合成替身。"""

from datetime import datetime, timedelta
import pytest
from sqlalchemy import select
from app.models.models import Base, BackgroundJob, KnowledgeVersion
from app.repositories.knowledge_repository import KnowledgeRepository, KnowledgeConflict
from app.services.knowledge_base import KnowledgeBaseService
from app.services.knowledge_base.index_worker import index_job
from app.services.knowledge_base.lifecycle import embedding_identity
from scripts.reindex_local_models import activate_staged


@pytest.fixture
def staged(tmp_path, monkeypatch):
    from app.config.config import settings

    monkeypatch.setattr(settings, "USE_MILVUS", False)

    class Embedding:
        _dim = 3
        backend_name = "synthetic-switch"

        def encode_batch(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

    repo = KnowledgeRepository("sqlite:///" + (tmp_path / "switch.db").as_posix())
    Base.metadata.create_all(repo.engine)
    kb = KnowledgeBaseService(repository=repo, embedding=Embedding())
    kb.initialize()

    def index(result):
        with repo.transaction() as db:
            job = db.get(BackgroundJob, result["job_id"])
            job.status = "running"
            job.lease_token = "synthetic-owner"
            job.lease_expires_at = datetime.utcnow() + timedelta(seconds=60)
        index_job(repo, kb._embedding_service, None, result["job_id"], "synthetic-owner")

    items = []
    for name in ("合成甲", "合成乙"):
        original = kb.add_document(name, "原发布政策")
        index(original)
        new = repo.save(document_id=original["document_id"], expected_version=1,
                        title=name, content="新政策", category="policy",
                        keywords=[], source="synthetic", publish=False)
        index(new)
        items.append({"old": {"id": original["document_id"], "published_version": 1}, "new": new})
    yield repo, kb, items, embedding_identity(kb._embedding_service)[0]
    repo.engine.dispose()


def test_failed_second_document_rolls_back_first_pointer(staged):
    repo, kb, items, fingerprint = staged
    with repo.transaction() as db:
        db.get(KnowledgeVersion, items[1]["new"]["version_id"]).status = "failed"
    with pytest.raises(RuntimeError, match="new_version_not_ready"):
        activate_staged(repo, items, fingerprint)
    assert {c["version"] for c in repo.snapshot()[1]} == {1}
    with repo.transaction() as db:
        versions = db.scalars(select(KnowledgeVersion)).all()
        assert len(versions) == 4


def test_unpublish_during_rebuild_is_not_reactivated(staged):
    repo, kb, items, fingerprint = staged
    kb.set_document_published(items[1]["old"]["id"], False)
    with pytest.raises(KnowledgeConflict):
        activate_staged(repo, items, fingerprint)
    assert kb.document_detail(items[1]["old"]["id"])["published_version"] is None
    assert kb.document_detail(items[0]["old"]["id"])["published_version"] == 1


def test_all_ready_switches_together_and_keeps_old_versions(staged):
    repo, kb, items, fingerprint = staged
    activate_staged(repo, items, fingerprint)
    assert {c["version"] for c in repo.snapshot()[1]} == {2}
    for item in items:
        assert kb.document_detail(item["old"]["id"], 1)["content"] == "原发布政策"
