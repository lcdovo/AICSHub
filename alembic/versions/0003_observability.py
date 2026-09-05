"""第6步：统一请求 Trace、Span 与评测元数据。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003_observability"
down_revision = "0002_rag_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    for table, columns in {
        "agent_traces": {
            "request_trace_id": sa.Column("request_trace_id", sa.String(100), nullable=True),
            "span_id": sa.Column("span_id", sa.String(100), nullable=True),
            "model_name": sa.Column("model_name", sa.String(100), nullable=True),
            "prompt_version": sa.Column("prompt_version", sa.String(50), nullable=True),
        },
        "evaluation_results": {
            "trace_id": sa.Column("trace_id", sa.String(100), nullable=True),
            "intent": sa.Column("intent", sa.String(50), nullable=True),
            "evaluator": sa.Column("evaluator", sa.String(50), nullable=True),
            "model_name": sa.Column("model_name", sa.String(100), nullable=True),
            "prompt_version": sa.Column("prompt_version", sa.String(50), nullable=True),
            "latency_ms": sa.Column("latency_ms", sa.Integer(), nullable=True),
            "token_usage": sa.Column("token_usage", sa.JSON(), nullable=True),
            "user_query": sa.Column("user_query", sa.Text(), nullable=True),
            "response": sa.Column("response", sa.Text(), nullable=True),
            "categories": sa.Column("categories", sa.JSON(), nullable=True),
            "processed_at": sa.Column("processed_at", sa.DateTime(), nullable=True),
            "processing_action": sa.Column("processing_action", sa.String(50), nullable=True),
            "processing_note": sa.Column("processing_note", sa.Text(), nullable=True),
        },
    }.items():
        existing = {c["name"] for c in inspector.get_columns(table)}
        for name, column in columns.items():
            if name not in existing:
                op.add_column(table, column)
        for name in columns:
            index_name = f"ix_{table}_{name}"
            if name in {"request_trace_id", "span_id", "trace_id", "intent"}:
                try:
                    op.create_index(index_name, table, [name], unique=(name == "span_id"))
                except Exception:
                    pass


def downgrade():
    for table, names in (("evaluation_results", ("token_usage", "latency_ms", "prompt_version", "model_name", "evaluator", "trace_id")), ("agent_traces", ("prompt_version", "model_name", "span_id", "request_trace_id"))):
        for name in names:
            try:
                op.drop_column(table, name)
            except Exception:
                pass
