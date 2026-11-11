"""Persist explicit sourced communication preferences."""
from alembic import op
import sqlalchemy as sa
from app.models.models import UserMemoryEntry

revision = "0014_user_memory_entries"
down_revision = "0013_agent_reliability"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not all(inspector.has_table(name) for name in ("users", "sessions", "messages")):
        return  # standalone knowledge-only databases do not acquire the chat domain
    metadata = sa.MetaData()
    for name in ("users", "sessions", "messages"):
        sa.Table(name, metadata, autoload_with=bind, resolve_fks=False)
    table = UserMemoryEntry.__table__.to_metadata(metadata)
    for column, parent in (("user_id", "users"), ("source_session_id", "sessions"),
                           ("source_message_id", "messages")):
        table.c[column].type = metadata.tables[parent].c.id.type
    table.create(bind, checkfirst=True)


def downgrade():
    raise RuntimeError("保留用户记忆来源审计；不自动删除持久条目。")
