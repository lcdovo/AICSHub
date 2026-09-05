"""第3步：业务状态、通知和审计持久化。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from app.models.models import Base

revision = "0001_persistence"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    # 空库由同一份 SQLAlchemy 元数据初始化，保证 SQLite/MySQL 表结构一致。
    Base.metadata.create_all(bind=bind)
    inspector = inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("tickets")}
    if "session_id" not in cols: op.add_column("tickets", sa.Column("session_id", sa.String(36), nullable=True))
    if "resolution" not in cols: op.add_column("tickets", sa.Column("resolution", sa.Text(), nullable=True))
    if "idempotency_key" not in cols: op.add_column("tickets", sa.Column("idempotency_key", sa.String(128), nullable=True))

def downgrade():
    for table in ("user_feedback", "operation_audits", "notification_records", "handoff_requests"):
        op.drop_table(table)
