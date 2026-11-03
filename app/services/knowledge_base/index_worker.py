"""M3幂等索引阶段；外部写只写不可变version的chunkID，发布由SQL最终fence控制。"""

from datetime import datetime, timedelta
from app.config.config import settings
import hashlib
from sqlalchemy import select, update
from app.models.models import (
    BackgroundJob,
    KnowledgeDoc,
    KnowledgeVersion,
    KnowledgeChunk,
)
from app.services.embedding_service import validate_vector
from .lifecycle import source_chunks, embedding_identity


def owned(db, jid: str, token: str) -> BackgroundJob:
    result = db.execute(
        update(BackgroundJob)
        .where(
            BackgroundJob.id == jid,
            BackgroundJob.status == "running",
            BackgroundJob.lease_token == token,
            BackgroundJob.lease_expires_at > datetime.utcnow(),
        )
        .values(updated_at=datetime.utcnow())
    )
    if result.rowcount != 1:
        raise RuntimeError("job_lease_lost")
    return db.get(BackgroundJob, jid, populate_existing=True)


def index_job(repository, embedding, vector_store, jid: str, token: str) -> None:
    try:
        with repository.transaction() as db:
            job = owned(db, jid, token)
            version = db.get(KnowledgeVersion, job.resource_id)
            doc = db.get(KnowledgeDoc, version.document_id)
            if doc.deleted_at:
                raise ValueError("document_archived")
            if version.status in {"ready", "published"}:
                job.status, job.lease_token, job.lease_expires_at = (
                    "succeeded",
                    None,
                    None,
                )
                return
            version.status = "indexing"
            job.lease_expires_at = datetime.utcnow() + timedelta(seconds=120)
            content, vid, number, did = (
                version.content,
                version.id,
                version.number,
                doc.id,
            )
        chunks = source_chunks(content)
        if not chunks or len(chunks) > 10000:
            raise ValueError("chunk_count_invalid")
        for i, c in enumerate(chunks):
            c["id"] = "chunk_" + hashlib.sha256(f"{vid}:{i}".encode()).hexdigest()[:48]
        fingerprint, metadata = embedding_identity(embedding)
        vectors = []
        for offset in range(0, len(chunks), max(1, settings.EMBEDDING_BATCH_SIZE)):
            with repository.transaction() as db:
                current = owned(db, jid, token)
                current.lease_expires_at = datetime.utcnow() + timedelta(seconds=120)
            vectors.extend(
                embedding.encode_batch(
                    [
                        c["content"]
                        for c in chunks[offset : offset + settings.EMBEDDING_BATCH_SIZE]
                    ]
                )
            )
        if len(vectors) != len(chunks):
            raise ValueError("embedding_count_mismatch")
        vectors = [validate_vector(v, metadata["dim"]) for v in vectors]
        # 外部I/O前再次检查租约，回写/发布前再次检查；旧写仅留下不可检索旧version派生物。
        with repository.transaction() as db:
            owned(db, jid, token)
        if vector_store is not None:
            vector_store.write(vid, chunks, vectors, fingerprint, metadata["dim"])
        with repository.transaction() as db:
            job = owned(db, jid, token)
            doc = db.scalar(
                select(KnowledgeDoc).where(KnowledgeDoc.id == did).with_for_update()
            )
            if doc.deleted_at:
                raise ValueError("document_archived")
            version = db.get(KnowledgeVersion, vid)
            # 同version按chunk_id幂等写派生物，不删除旧原文或旧索引。
            existing = {
                c.chunk_id: c
                for c in db.scalars(
                    select(KnowledgeChunk).where(
                        KnowledgeChunk.document_id == did,
                        KnowledgeChunk.version == number,
                    )
                ).all()
            }
            if existing and set(existing) != {c["id"] for c in chunks}:
                raise ValueError("chunk_identity_conflict")
            for i, (c, v) in enumerate(zip(chunks, vectors)):
                if c["id"] in existing:
                    continue
                db.add(
                    KnowledgeChunk(
                        document_id=did,
                        chunk_id=c["id"],
                        content=c["content"],
                        embedding=v,
                        chunk_index=i,
                        version=number,
                        is_active=True,
                        content_hash=c["content_hash"],
                        start_offset=c["start_offset"],
                        end_offset=c["end_offset"],
                        section=c["section"],
                        chunk_policy=c["chunk_policy"],
                        source_context=c["source_context"],
                    )
                )
            version.embedding_fingerprint, version.embedding_metadata = (
                fingerprint,
                metadata,
            )
            version.status, version.error_code = "ready", None
            if version.publish_requested and doc.version == number:
                doc.published_version = number
                doc.is_published = True
                doc.lifecycle_status = "published"
                doc.title, doc.content, doc.category, doc.keywords, doc.source = (
                    version.title,
                    version.content,
                    version.category,
                    version.keywords,
                    version.source,
                )
                doc.chunk_count = len(chunks)
                version.status = "published"
                repository.bump(db)
            job.status, job.lease_token, job.lease_expires_at = "succeeded", None, None
    except Exception:
        with repository.transaction() as db:
            try:
                job = owned(db, jid, token)
            except RuntimeError:
                return
            version = db.get(KnowledgeVersion, job.resource_id)
            version.status, version.error_code = (
                "failed",
                "index_dependency_or_contract_error",
            )
            job.status, job.last_error, job.lease_token, job.lease_expires_at = (
                "failed",
                "index_dependency_or_contract_error",
                None,
                None,
            )
