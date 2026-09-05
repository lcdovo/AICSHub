"""Add LLM governance version metadata to traces and evaluations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0004_llm_governance"
down_revision = "0003_observability"
branch_labels = None
depends_on = None


def upgrade():
    inspector = inspect(op.get_bind())
    for table in ("agent_traces", "evaluation_results"):
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name in ("schema_version", "route_version"):
            if name not in existing:
                op.add_column(table, sa.Column(name, sa.String(50), nullable=True))


def downgrade():
    for table in ("evaluation_results", "agent_traces"):
        for name in ("route_version", "schema_version"):
            try:
                op.drop_column(table, name)
            except Exception:
                pass
