"""稳定文档/不可变版本与发布指针；保留旧原文/chunk，不猜旧Embedding空间。"""

import uuid
import json
from datetime import datetime
from alembic import op
import sqlalchemy as sa
from app.models.models import KnowledgeVersion, KnowledgeEpoch

revision = "0009_m3_knowledge_versions"
down_revision = "0008_m2_runs_jobs"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("knowledge_docs")}
    for col in [
        sa.Column("stable_id", sa.String(40), nullable=True),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column(
            "lifecycle_status", sa.String(20), nullable=False, server_default="legacy"
        ),
    ]:
        if col.name not in columns:
            op.add_column("knowledge_docs", col)
    if "ix_knowledge_docs_stable_id" not in {
        i["name"] for i in sa.inspect(bind).get_indexes("knowledge_docs")
    }:
        op.create_index(
            "ix_knowledge_docs_stable_id", "knowledge_docs", ["stable_id"], unique=True
        )
    columns = {c["name"] for c in sa.inspect(bind).get_columns("knowledge_chunks")}
    for col in [
        sa.Column("content_hash", sa.String(64)),
        sa.Column("start_offset", sa.Integer()),
        sa.Column("end_offset", sa.Integer()),
        sa.Column("section", sa.String(200)),
        sa.Column("chunk_policy", sa.String(40)),
        sa.Column("source_context", sa.JSON()),
    ]:
        if col.name not in columns:
            op.add_column("knowledge_chunks", col)
    KnowledgeVersion.__table__.create(bind, checkfirst=True)
    KnowledgeEpoch.__table__.create(bind, checkfirst=True)
    docs = sa.table(
        "knowledge_docs",
        sa.column("id"),
        sa.column("stable_id"),
        sa.column("version"),
        sa.column("title"),
        sa.column("content"),
        sa.column("category"),
        sa.column("keywords"),
        sa.column("source"),
        sa.column("lifecycle_status"),
        sa.column("published_version"),
        sa.column("is_published"),
        sa.column("deleted_at"),
    )
    for row in bind.execute(sa.select(docs)).mappings().all():
        stable = (
            row["stable_id"]
            or "kb_"
            + uuid.uuid5(
                uuid.NAMESPACE_URL, "aicshub:legacy-document:" + str(row["id"])
            ).hex
        )
        bind.execute(
            docs.update()
            .where(docs.c.id == row["id"])
            .values(stable_id=stable, lifecycle_status="legacy_unverified")
        )
        number = row["version"] or 1
        existing = bind.execute(
            sa.select(KnowledgeVersion.__table__.c.id).where(
                KnowledgeVersion.__table__.c.document_id == row["id"],
                KnowledgeVersion.__table__.c.number == number,
            )
        ).first()
        if existing is None:
            keywords = row["keywords"]
            if isinstance(keywords, str):
                keywords = json.loads(keywords)
            bind.execute(
                KnowledgeVersion.__table__.insert().values(
                    id=str(uuid.uuid4()),
                    document_id=row["id"],
                    number=number,
                    title=row["title"],
                    content=row["content"],
                    category=row["category"] or "",
                    keywords=keywords or [],
                    source=row["source"] or "",
                    status="legacy_unverified",
                    publish_requested=False,
                    chunk_policy="legacy",
                    created_at=datetime.utcnow(),
                )
            )
        # 原已发布正文保留SQL关键词降级；旧向量指纹未知，禁止跨空间向量检索。
        if row["is_published"] and row["deleted_at"] is None:
            bind.execute(
                docs.update()
                .where(docs.c.id == row["id"])
                .values(published_version=number)
            )
    if (
        bind.execute(
            sa.select(KnowledgeEpoch.__table__.c.id).where(
                KnowledgeEpoch.__table__.c.id == 1
            )
        ).first()
        is None
    ):
        bind.execute(KnowledgeEpoch.__table__.insert().values(id=1, value=0))


def downgrade():
    raise RuntimeError("知识版本/原文不可有损自动降级，请恢复已验证备份")
