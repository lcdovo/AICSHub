"""新增模拟退换货申请表，不修改订单或执行支付。"""
from alembic import op
from app.models.models import RefundApplication

revision = "0006_refund_applications"
down_revision = "0005_handoff_capacity"
branch_labels = None
depends_on = None


def upgrade():
    RefundApplication.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("refund_applications")
