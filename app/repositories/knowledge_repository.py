"""SQLAlchemy知识事实仓储；同步仅在线程/独立worker使用，SQL为发布过滤事实源。"""

from __future__ import annotations
from app.utils.utc_time import utc_now_db
from contextlib import contextmanager
from typing import Iterator
from datetime import datetime
import uuid
from sqlalchemy import create_engine, select, update, event, func
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.engine import make_url
from app.config.config import settings
from app.models.models import (
    KnowledgeDoc,
    KnowledgeVersion,
    KnowledgeChunk,
    KnowledgeEpoch,
    BackgroundJob,
)


class KnowledgeConflict(RuntimeError):
    pass


class KnowledgeRepository:
    def __init__(self, url: str | None = None) -> None:
        url = make_url(url or settings.DATABASE_URL)
        url = url.set(
            drivername="sqlite"
            if url.drivername.startswith("sqlite")
            else "mysql+pymysql"
        )
        self.engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"timeout": 20} if url.drivername == "sqlite" else {},
        )
        if url.drivername == "sqlite":

            @event.listens_for(self.engine, "connect")
            def configure(connection, _):
                connection.isolation_level = None

            @event.listens_for(self.engine, "begin")
            def begin(connection):
                connection.exec_driver_sql("BEGIN")

    @contextmanager
    def transaction(self) -> Iterator[DBSession]:
        with DBSession(self.engine, expire_on_commit=False) as db:
            with db.begin():
                yield db

    @staticmethod
    def find(db: DBSession, stable_id: str) -> KnowledgeDoc | None:
        if stable_id.startswith(("doc_db_", "doc_mysql_")):
            raw = stable_id.rsplit("_", 1)[-1]
            return db.get(KnowledgeDoc, int(raw)) if raw.isdecimal() else None
        return db.scalar(
            select(KnowledgeDoc).where(KnowledgeDoc.stable_id == stable_id)
        )

    @staticmethod
    def bump(db: DBSession) -> None:
        dialect = db.get_bind().dialect.name
        if dialect == "mysql":
            from sqlalchemy.dialects.mysql import insert

            statement = insert(KnowledgeEpoch).values(id=1, value=1)
            statement = statement.on_duplicate_key_update(
                value=KnowledgeEpoch.value + 1
            )
        else:
            from sqlalchemy.dialects.sqlite import insert

            statement = (
                insert(KnowledgeEpoch)
                .values(id=1, value=1)
                .on_conflict_do_update(
                    index_elements=["id"], set_={"value": KnowledgeEpoch.value + 1}
                )
            )
        db.execute(statement)

    def save(
        self,
        *,
        title,
        content,
        category="",
        keywords=None,
        source="",
        document_id=None,
        expected_version=None,
        publish=True,
        owner_id=None,
    ):
        with self.transaction() as db:
            if document_id:
                doc = self.find(db, document_id)
                if doc is None or doc.deleted_at:
                    raise KeyError("document_not_found")
                expected = doc.version if expected_version is None else expected_version
                changed = db.execute(
                    update(KnowledgeDoc)
                    .where(
                        KnowledgeDoc.id == doc.id,
                        KnowledgeDoc.version == expected,
                        KnowledgeDoc.deleted_at.is_(None),
                    )
                    .values(version=expected + 1, lifecycle_status="draft")
                )
                if changed.rowcount != 1:
                    raise KnowledgeConflict("document_version_conflict")
                number = expected + 1
            else:
                doc = KnowledgeDoc(
                    stable_id="kb_" + uuid.uuid4().hex,
                    title=title,
                    content=content,
                    category=category,
                    keywords=keywords or [],
                    source=source,
                    version=1,
                    is_published=False,
                    lifecycle_status="draft",
                )
                db.add(doc)
                db.flush()
                number = 1
            version = KnowledgeVersion(
                id=str(uuid.uuid4()),
                document_id=doc.id,
                number=number,
                title=title,
                content=content,
                category=category,
                keywords=keywords or [],
                source=source,
                publish_requested=publish,
            )
            db.add(version)
            db.flush()
            job = BackgroundJob(
                id=str(uuid.uuid4()),
                kind="knowledge_index",
                resource_id=version.id,
                user_id=owner_id,
                event_key="knowledge_index:" + version.id,
            )
            db.add(job)
            db.flush()
            return {
                "success": True,
                "document_id": doc.stable_id,
                "version": number,
                "version_id": version.id,
                "job_id": job.id,
                "status": "draft",
                "chunks_count": 0,
                "published_version": doc.published_version,
            }

    def detail(self, document_id: str, version: int | None = None) -> dict:
        with self.transaction() as db:
            doc = self.find(db, document_id)
            if doc is None:
                raise KeyError("document_not_found")
            number = version or doc.version
            item = db.scalar(
                select(KnowledgeVersion).where(
                    KnowledgeVersion.document_id == doc.id,
                    KnowledgeVersion.number == number,
                )
            )
            if item is None:
                raise KeyError("version_not_found")
            return {
                "document_id": doc.stable_id,
                "version_id": item.id,
                "version": item.number,
                "title": item.title,
                "content": item.content,
                "category": item.category,
                "keywords": item.keywords or [],
                "source": item.source,
                "status": item.status,
                "published_version": doc.published_version,
                "deleted": doc.deleted_at is not None,
                "error_code": item.error_code,
                "embedding_fingerprint": item.embedding_fingerprint,
                "embedding_metadata": item.embedding_metadata,
                "publish_requested": item.publish_requested,
                "publication_enabled": doc.lifecycle_status != "archived"
                and (doc.published_version is not None or item.publish_requested),
            }

    def publish(self, document_id, published=True, version=None, expected_version=None):
        with self.transaction() as db:
            doc = self.find(db, document_id)
            if doc is None or doc.deleted_at:
                return False
            expected = doc.version if expected_version is None else expected_version
            # 锁住当前版本；编辑者不能在校验后覆盖切换。
            owned = db.execute(
                update(KnowledgeDoc)
                .where(
                    KnowledgeDoc.id == doc.id,
                    KnowledgeDoc.version == expected,
                    KnowledgeDoc.deleted_at.is_(None),
                )
                .values(updated_at=utc_now_db())
            )
            if owned.rowcount != 1:
                raise KnowledgeConflict("document_version_conflict")
            item = db.scalar(
                select(KnowledgeVersion).where(
                    KnowledgeVersion.document_id == doc.id,
                    KnowledgeVersion.number == (version or expected),
                )
            )
            if published and (
                item is None or item.status not in {"ready", "published"}
            ):
                raise KnowledgeConflict("index_not_ready")
            doc.published_version = item.number if published else None
            doc.is_published = published
            doc.lifecycle_status = "published" if published else "archived"
            if item and published:
                item.status = "published"
                doc.title, doc.content, doc.category, doc.keywords, doc.source = (
                    item.title,
                    item.content,
                    item.category,
                    item.keywords,
                    item.source,
                )
                doc.chunk_count = db.scalar(
                    select(func.count())
                    .select_from(KnowledgeChunk)
                    .where(
                        KnowledgeChunk.document_id == doc.id,
                        KnowledgeChunk.version == item.number,
                    )
                )
            self.bump(db)
            return True

    def delete(self, document_id: str) -> bool:
        with self.transaction() as db:
            doc = self.find(db, document_id)
            if doc is None or doc.deleted_at:
                return False
            db.execute(
                update(KnowledgeDoc)
                .where(KnowledgeDoc.id == doc.id)
                .values(
                    deleted_at=utc_now_db(),
                    is_published=False,
                    published_version=None,
                    lifecycle_status="archived",
                    version=KnowledgeDoc.version + 1,
                )
            )
            self.bump(db)
            return True

    def documents(self) -> list[dict]:
        with self.transaction() as db:
            docs = db.scalars(
                select(KnowledgeDoc)
                .where(KnowledgeDoc.deleted_at.is_(None))
                .order_by(KnowledgeDoc.id)
            ).all()
            versions = {
                v.document_id: v
                for v in db.scalars(
                    select(KnowledgeVersion).join(
                        KnowledgeDoc,
                        (KnowledgeDoc.id == KnowledgeVersion.document_id)
                        & (KnowledgeDoc.version == KnowledgeVersion.number),
                    )
                ).all()
            }
            return [
                {
                    "id": d.stable_id,
                    "title": versions[d.id].title if d.id in versions else d.title,
                    "category": versions[d.id].category
                    if d.id in versions
                    else d.category,
                    "source": versions[d.id].source if d.id in versions else d.source,
                    "version": d.version,
                    "published_version": d.published_version,
                    "status": "archived"
                    if d.lifecycle_status == "archived"
                    else (
                        versions[d.id].status
                        if d.id in versions
                        else d.lifecycle_status
                    ),
                    "error_code": versions[d.id].error_code
                    if d.id in versions
                    else None,
                    "chunks_count": d.chunk_count,
                    "keywords": (
                        versions[d.id].keywords if d.id in versions else d.keywords
                    )
                    or [],
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                    "updated_at": d.updated_at.isoformat() if d.updated_at else None,
                }
                for d in docs
            ]

    def current_epoch(self) -> int:
        with self.transaction() as db:
            return db.scalar(select(KnowledgeEpoch.value).where(KnowledgeEpoch.id == 1)) or 0

    def snapshot(self) -> tuple[int, list[dict]]:
        with self.transaction() as db:
            epoch = db.get(KnowledgeEpoch, 1)
            revision = epoch.value if epoch else 0
            rows = db.execute(
                select(KnowledgeDoc, KnowledgeVersion, KnowledgeChunk)
                .join(
                    KnowledgeVersion,
                    (KnowledgeVersion.document_id == KnowledgeDoc.id)
                    & (KnowledgeVersion.number == KnowledgeDoc.published_version),
                )
                .join(
                    KnowledgeChunk,
                    (KnowledgeChunk.document_id == KnowledgeDoc.id)
                    & (KnowledgeChunk.version == KnowledgeVersion.number),
                )
                .where(
                    KnowledgeDoc.deleted_at.is_(None),
                    KnowledgeDoc.is_published.is_(True),
                    KnowledgeVersion.status.in_(
                        ["ready", "published", "legacy_unverified"]
                    ),
                )
                .order_by(KnowledgeDoc.id, KnowledgeChunk.chunk_index, KnowledgeChunk.id)
                .limit(10001)
            ).all()
            if len(rows) > 10000:
                raise RuntimeError("knowledge_snapshot_budget_exceeded")
            chunks = [
                {
                    "id": c.chunk_id,
                    "document_id": d.stable_id,
                    "version_id": v.id,
                    "version": v.number,
                    "title": v.title,
                    "content": c.content,
                    "category": v.category,
                    "keywords": v.keywords or [],
                    "source": v.source,
                    "section": c.section,
                    "start_offset": c.start_offset,
                    "end_offset": c.end_offset,
                    "content_hash": c.content_hash,
                    "chunk_policy": c.chunk_policy,
                    "source_context": c.source_context,
                    "embedding_fingerprint": v.embedding_fingerprint,
                    "_embedding": c.embedding,
                }
                for d, v, c in rows
            ]
            return revision, chunks

    def archive_all(self):
        with self.transaction() as db:
            db.execute(
                update(KnowledgeDoc)
                .where(KnowledgeDoc.deleted_at.is_(None))
                .values(
                    deleted_at=utc_now_db(),
                    is_published=False,
                    published_version=None,
                    lifecycle_status="archived",
                    version=KnowledgeDoc.version + 1,
                )
            )
            self.bump(db)
        return {"success": True, "status": "archived", "retained_history": True}
