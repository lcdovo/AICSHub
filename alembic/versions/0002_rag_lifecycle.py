"""第5步：知识库文档版本、chunk 与索引生命周期。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0002_rag_lifecycle"
down_revision = "0001_persistence"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("knowledge_docs")}
    additions = {
        "keywords": sa.Column("keywords", sa.JSON(), nullable=True),
        "source": sa.Column("source", sa.String(500), nullable=True),
        "deleted_at": sa.Column("deleted_at", sa.DateTime(), nullable=True),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("knowledge_docs", column)

    if "knowledge_chunks" not in inspector.get_table_names():
        op.create_table(
            "knowledge_chunks",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("document_id", sa.Integer(), sa.ForeignKey("knowledge_docs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("chunk_id", sa.String(100), nullable=False, unique=True),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("embedding", sa.JSON(), nullable=True),
            sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])
        op.create_index("ix_knowledge_chunks_chunk_id", "knowledge_chunks", ["chunk_id"], unique=True)
        op.create_index("ix_knowledge_chunks_active", "knowledge_chunks", ["is_active"])


def downgrade():
    op.drop_table("knowledge_chunks")
    for name in ("deleted_at", "source", "keywords"):
        op.drop_column("knowledge_docs", name)
