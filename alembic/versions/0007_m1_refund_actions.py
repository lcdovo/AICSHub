"""持久退款action、业务去重闸门与精确订单金额/签收字段。"""
from decimal import Decimal
from alembic import op
import sqlalchemy as sa
from app.models.models import RefundAction, RefundBusinessClaim

revision = "0007_m1_refund_actions"
down_revision = "0006_refund_applications"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT order_id,total_amount FROM orders")).all()
    for order_id, amount in rows:
        value = Decimal(str(amount))
        if not value.is_finite() or value < 0 or value >= Decimal("10000000000") or value != value.quantize(Decimal("0.01")):
            raise ValueError("订单金额存在不可精确迁移值；停止迁移，需人工核查")
    columns = {c["name"] for c in sa.inspect(bind).get_columns("orders")}
    with op.batch_alter_table("orders") as batch:
        batch.alter_column("total_amount", existing_type=sa.Float(), type_=sa.Numeric(12, 2), existing_nullable=False)
        if "refunded_amount" not in columns:
            batch.add_column(sa.Column("refunded_amount", sa.Numeric(12, 2), nullable=False, server_default="0"))
        if "received_at" not in columns:
            batch.add_column(sa.Column("received_at", sa.DateTime(), nullable=True))
        if "currency" not in columns:
            batch.add_column(sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"))
    columns = {c["name"] for c in sa.inspect(bind).get_columns("refund_applications")}
    for name, kind in [("action_id", sa.String(36)), ("business_hash", sa.String(64)),
                       ("reviewed_at", sa.DateTime()), ("review_note", sa.Text())]:
        if name not in columns:
            op.add_column("refund_applications", sa.Column(name, kind, nullable=True))
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("refund_applications")}
    if "uq_refund_application_action" not in indexes:
        op.create_index("uq_refund_application_action", "refund_applications", ["action_id"], unique=True)
    RefundAction.__table__.create(bind, checkfirst=True)
    RefundBusinessClaim.__table__.create(bind, checkfirst=True)


def downgrade():
    raise RuntimeError("此迁移包含持久动作和精确金额，拒绝有损自动降级；请使用验收过的备份恢复")
