"""模型尝试/预算预留/全worker并发槽，无用户原文。"""

from alembic import op
from app.models.models import ModelBudgetDay, ModelCallRecord, ModelConcurrencySlot

revision = "0010_m4_model_gateway"
down_revision = "0009_m3_knowledge_versions"
branch_labels = None
depends_on = None


def upgrade():
    for model in (ModelBudgetDay, ModelCallRecord, ModelConcurrencySlot):
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError("模型预算和账单事实不可自动有损降级")
