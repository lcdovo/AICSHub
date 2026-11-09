"""能力执行账本；保留旧任务、会话及业务回执。"""
from alembic import op
import sqlalchemy as sa
from app.models.models import AgentCapabilityCall

revision = "0013_agent_reliability"
down_revision = "0012_agent_tasks"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("chat_runs") or inspector.has_table("agent_capability_calls"):
        return
    if bind.dialect.name == "mysql":
        # Restored/legacy tables may use a different collation than the database
        # default. MySQL requires FK columns to match their actual parent types.
        metadata = sa.MetaData()
        for name in ("users", "sessions", "chat_runs"):
            sa.Table(name, metadata, autoload_with=bind, resolve_fks=False)
        table = AgentCapabilityCall.__table__.to_metadata(metadata)
        for column, parent in (("run_id", "chat_runs"), ("user_id", "users"), ("session_id", "sessions")):
            table.c[column].type = metadata.tables[parent].c.id.type
        table.create(bind, checkfirst=True)
    else:
        AgentCapabilityCall.__table__.create(bind, checkfirst=True)


def downgrade():
    raise RuntimeError("执行账本不可自动有损降级；回退应用时保留执行审计。")
