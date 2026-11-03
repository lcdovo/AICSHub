"""对现有已发布知识构建新Embedding空间；全部ready后才在同一SQL事务切发布指针。

只输出计数/指纹/版本引用，不打印原文或凭据。旧文档版本和collection保留。
"""

from pathlib import Path
import sys, json, uuid
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func
from app.models.models import (
    KnowledgeDoc,
    KnowledgeVersion,
    KnowledgeChunk,
    BackgroundJob,
)
from app.repositories.knowledge_repository import KnowledgeConflict
from app.services.knowledge_base import KnowledgeBaseService
from app.services.knowledge_base.index_worker import index_job
from app.services.knowledge_base.lifecycle import embedding_identity


def activate_staged(repo, staged, fingerprint):
    """所有版本ready且发布指针未变化时原子激活；异常整体回滚。"""
    # One transaction across documents. No changes to active pointer until all indices verify.
    with repo.transaction() as db:
        for item in staged:
            old, new = item["old"], item["new"]
            d = db.scalar(
                select(KnowledgeDoc)
                .where(KnowledgeDoc.stable_id == old["id"])
                .with_for_update()
            )
            if (
                d.deleted_at
                or d.version != new["version"]
                or d.published_version != old["published_version"]
            ):
                raise KnowledgeConflict("publication_changed_during_rebuild")
            v = db.get(KnowledgeVersion, new["version_id"])
            if v.status != "ready" or v.embedding_fingerprint != fingerprint:
                raise RuntimeError("new_version_not_ready")
            d.published_version = v.number
            d.lifecycle_status = "published"
            d.is_published = True
            v.status = "published"
            v.publish_requested = True
            d.chunk_count = db.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(
                    KnowledgeChunk.document_id == d.id,
                    KnowledgeChunk.version == v.number,
                )
            )
        repo.bump(db)


def main():
    kb = KnowledgeBaseService()
    kb.initialize()
    repo = kb._repository
    if not kb._vector_store:
        raise RuntimeError("required_new_vector_index_unavailable")
    fingerprint, metadata = embedding_identity(kb._embedding_service)
    with repo.transaction() as db:
        originals = [
            {
                "id": d.stable_id,
                "version": d.version,
                "published_version": d.published_version,
                "title": d.title,
                "content": d.content,
                "category": d.category,
                "keywords": d.keywords or [],
                "source": d.source or "",
            }
            for d in db.scalars(
                select(KnowledgeDoc).where(
                    KnowledgeDoc.deleted_at.is_(None),
                    KnowledgeDoc.is_published.is_(True),
                )
            ).all()
        ]
    staged = []
    for old in originals:
        result = repo.save(
            document_id=old["id"],
            expected_version=old["version"],
            title=old["title"],
            content=old["content"],
            category=old["category"],
            keywords=old["keywords"],
            source=old["source"],
            publish=False,
        )
        token = str(uuid.uuid4())
        with repo.transaction() as db:
            job = db.get(BackgroundJob, result["job_id"])
            job.status = "running"
            job.lease_token = token
            job.lease_expires_at = datetime.utcnow() + timedelta(seconds=120)
            job.attempts += 1
        index_job(
            repo, kb._embedding_service, kb._vector_store, result["job_id"], token
        )
        new = repo.detail(old["id"], result["version"])
        if new["status"] != "ready" or new["embedding_fingerprint"] != fingerprint:
            raise RuntimeError("new_index_not_ready_no_pointer_switch")
        staged.append({"old": old, "new": result})
    activate_staged(repo, staged, fingerprint)
    epoch, chunks = repo.snapshot()
    if len({c["document_id"] for c in chunks}) != len(originals) or any(
        c["embedding_fingerprint"] != fingerprint for c in chunks
    ):
        raise RuntimeError("active_pointer_verification_failed")
    result = {
        "status": "PASS",
        "documents": len(originals),
        "active_chunks": len(chunks),
        "epoch": epoch,
        "embedding_fingerprint": fingerprint,
        "model": metadata["model"],
        "dim": metadata["dim"],
        "collection": kb._vector_store.collection(fingerprint),
        "versions": [
            {
                "document_id": i["old"]["id"],
                "old_published": i["old"]["published_version"],
                "new_published": i["new"]["version"],
            }
            for i in staged
        ],
        "old_data_retained": True,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
