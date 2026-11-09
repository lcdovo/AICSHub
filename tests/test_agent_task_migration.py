"""新空库与旧0011增量路径；只操作pytest临时SQLite。"""
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.models.models import Base


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0012_agent_tasks.py"
    spec = spec_from_file_location("task_migration", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_preserves_legacy_rows_and_is_repeatable(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "legacy.db").as_posix())
    try:
        with engine.begin() as db:
            for name in ("users", "sessions", "chat_runs"):
                db.execute(text(f"CREATE TABLE {name} (id VARCHAR(36) PRIMARY KEY)"))
            db.execute(text("CREATE TABLE refund_actions (id VARCHAR(36) PRIMARY KEY, reason TEXT)"))
            db.execute(text("INSERT INTO refund_actions VALUES ('existing', '原动作')"))
            with Operations.context(MigrationContext.configure(db)):
                migration().upgrade()
                migration().upgrade()
            assert {"agent_tasks", "agent_task_steps"} <= set(inspect(db).get_table_names())
            assert "preconditions" in {c["name"] for c in inspect(db).get_columns("refund_actions")}
            assert db.execute(text("SELECT reason, preconditions FROM refund_actions")).one() == ("原动作", None)
            assert inspect(db).get_unique_constraints("agent_task_steps")[0]["column_names"] == ["task_id", "run_key", "sequence"]
    finally:
        engine.dispose()


def test_current_metadata_empty_database_can_upgrade_without_duplicate_tables(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "fresh.db").as_posix())
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as db:
            with Operations.context(MigrationContext.configure(db)):
                migration().upgrade()
            assert {"agent_tasks", "agent_task_steps"} <= set(inspect(db).get_table_names())
    finally:
        engine.dispose()


def test_standalone_knowledge_sqlite_does_not_require_refund_domain(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "knowledge-only.db").as_posix())
    try:
        with engine.begin() as db:
            db.execute(text("CREATE TABLE knowledge_docs (id INTEGER PRIMARY KEY, content TEXT)"))
            db.execute(text("INSERT INTO knowledge_docs VALUES (1, '保留原知识正文')"))
            with Operations.context(MigrationContext.configure(db)):
                migration().upgrade()
            assert db.execute(text("SELECT content FROM knowledge_docs")).scalar_one() == "保留原知识正文"
            assert "agent_tasks" not in inspect(db).get_table_names()
    finally:
        engine.dispose()
