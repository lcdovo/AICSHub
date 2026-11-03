"""新增座席分配事务闸门，不改写历史指派或演示座席规则。"""
from alembic import op
import sqlalchemy as sa

revision = "0005_handoff_capacity"
down_revision = "0004_llm_governance"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("handoff_capacity_gates"):
        op.create_table("handoff_capacity_gates",
                        sa.Column("agent_id", sa.String(50), primary_key=True),
                        sa.Column("revision", sa.Integer, nullable=False, server_default="0"))


def downgrade():
    op.drop_table("handoff_capacity_gates")
