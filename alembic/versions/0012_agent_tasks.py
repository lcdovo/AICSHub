"""任务进度与Run步骤审计；保留旧会话、动作及回执。"""
from alembic import op
import sqlalchemy as sa
from app.models.models import AgentTask, AgentTaskStep

revision = "0012_agent_tasks"
down_revision = "0011_m4_model_revision"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if bind.dialect.name == "sqlite" and not inspector.has_table("chat_runs"):
        # 独立知识库兼容库不包含聊天业务域，不能假设存在退款表。
        return
    if inspector.has_table("refund_actions") and "preconditions" not in {
        c["name"] for c in inspector.get_columns("refund_actions")
    }:
        op.add_column("refund_actions", sa.Column("preconditions", sa.JSON(), nullable=True))
    # 初始迁移按当前metadata建空库；已有0011库则增量建表，两种路径都可重复运行。
    for model in (AgentTask, AgentTaskStep):
        model.__table__.create(bind, checkfirst=True)


def downgrade():
    raise RuntimeError("任务及执行审计不可自动有损降级；关闭任务开关可回退应用。")
