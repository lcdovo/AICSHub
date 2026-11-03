"""补记模型revision；既有尝试保持unknown，不伪造历史revision。"""

from alembic import op
import sqlalchemy as sa

revision = "0011_m4_model_revision"
down_revision = "0010_m4_model_gateway"
branch_labels = None
depends_on = None


def upgrade():
    if "model_version" not in {
        c["name"] for c in sa.inspect(op.get_bind()).get_columns("model_calls")
    }:
        op.add_column(
            "model_calls", sa.Column("model_version", sa.String(160), nullable=True)
        )

    if "price_snapshot" not in {
        c["name"] for c in sa.inspect(op.get_bind()).get_columns("model_calls")
    }:
        op.add_column(
            "model_calls", sa.Column("price_snapshot", sa.JSON(), nullable=True)
        )


def downgrade():
    raise RuntimeError("模型计量revision不可自动有损降级")
