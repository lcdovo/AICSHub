"""SQL会话版本/租约、Run事件、Job与模拟Outbox回执；不回填伪历史Run。"""
from alembic import op
import sqlalchemy as sa
from app.models.models import ChatRun, RunEvent, BackgroundJob, SimulatedDelivery
revision = "0008_m2_runs_jobs"
down_revision = "0007_m1_refund_actions"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("sessions")}
    fields = [sa.Column("summary_cursor",sa.Integer(),nullable=False,server_default="0"),
              sa.Column("summary_version",sa.Integer(),nullable=False,server_default="0"),
              sa.Column("summary_policy",sa.String(40),nullable=False,server_default="rules-v1"),
              sa.Column("version",sa.Integer(),nullable=False,server_default="0"),
              sa.Column("context_data",sa.JSON(),nullable=True),sa.Column("lease_token",sa.String(36),nullable=True),
              sa.Column("lease_expires_at",sa.DateTime(),nullable=True),sa.Column("deleted_at",sa.DateTime(),nullable=True)]
    for col in fields:
        if col.name not in columns: op.add_column("sessions",col)
    columns = {c["name"] for c in sa.inspect(bind).get_columns("messages")}
    if "run_id" not in columns:
        op.add_column("messages",sa.Column("run_id",sa.String(36),nullable=True))
        op.create_index("ix_messages_run_id","messages",["run_id"])
    columns = {c["name"] for c in sa.inspect(bind).get_columns("notification_records")}
    if "is_simulated" not in columns: op.add_column("notification_records",sa.Column("is_simulated",sa.Boolean(),nullable=False,server_default=sa.false()))
    if "provider_receipt" not in columns: op.add_column("notification_records",sa.Column("provider_receipt",sa.String(128),nullable=True))
    if bind.dialect.name == "mysql":
        op.alter_column("notification_records","status",existing_type=sa.Enum("PENDING","SENT","FAILED"),
                        type_=sa.Enum("PENDING","SENDING","UNKNOWN","SENT","FAILED"),existing_nullable=False)
    for model in (ChatRun,RunEvent,BackgroundJob,SimulatedDelivery): model.__table__.create(bind,checkfirst=True)


def downgrade():
    raise RuntimeError("Run/回执/任务不可有损自动降级，请使用验证过的备份恢复")
