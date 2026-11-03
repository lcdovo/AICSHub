"""M3临时SQLite/合成Embedding，发布与回滚真实SQL；不代表模型效果。"""

from datetime import datetime, timedelta
import pytest
from sqlalchemy import select, func
from app.models.models import Base, BackgroundJob, KnowledgeChunk
from app.repositories.knowledge_repository import KnowledgeRepository, KnowledgeConflict
from app.services.knowledge_base import KnowledgeBaseService
from app.services.knowledge_base.index_worker import index_job


class SyntheticEmbedding:
    _dim = 3
    backend_name = "synthetic-v1"

    def __init__(self):
        self.calls = []
        self.fail = False

    def encode_batch(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("synthetic failure")
        return [[1.0, 0.0, 0.0] for _ in texts]

    def encode_query(self, text):
        return [1.0, 0.0, 0.0]


@pytest.fixture
def kb(tmp_path, monkeypatch):
    from app.config.config import settings

    monkeypatch.setattr(settings, "USE_MILVUS", False)
    repo = KnowledgeRepository("sqlite:///" + (tmp_path / "knowledge.db").as_posix())
    Base.metadata.create_all(repo.engine)
    embedding = SyntheticEmbedding()
    service = KnowledgeBaseService(repository=repo, embedding=embedding)
    service.initialize()
    yield service
    repo.engine.dispose()


def execute(kb, result):
    with kb._repository.transaction() as db:
        job = db.get(BackgroundJob, result["job_id"])
        job.status = "running"
        job.lease_token = "synthetic-owner"
        job.lease_expires_at = datetime.utcnow() + timedelta(seconds=30)
        job.attempts += 1
    index_job(
        kb._repository,
        kb._embedding_service,
        kb._vector_store,
        result["job_id"],
        "synthetic-owner",
    )


def test_stable_id_restart_version_and_same_title_isolation(kb):
    first = kb.add_document("同名", "第一版本政策")
    other = kb.add_document("同名", "另一个文档")
    assert first["status"] == "draft" and first["published_version"] is None
    execute(kb, first)
    execute(kb, other)
    restarted = KnowledgeBaseService(
        repository=kb._repository, embedding=kb._embedding_service
    )
    updated = restarted.update_document(
        first["document_id"], content="第二版本政策", expected_version=1
    )
    execute(kb, updated)
    assert updated["document_id"] == first["document_id"] and updated["version"] == 2
    assert (
        restarted.document_detail(first["document_id"], 1)["content"] == "第一版本政策"
    )
    assert restarted.document_detail(other["document_id"])["version"] == 1
    assert restarted.delete_document(first["document_id"])
    assert restarted.document_detail(other["document_id"])["published_version"] == 1
    _, chunks = kb._repository.snapshot()
    assert {c["document_id"] for c in chunks} == {other["document_id"]}


def test_failed_new_index_preserves_published_old_version_and_cas(kb):
    first = kb.add_document("政策", "原发布版")
    execute(kb, first)
    second = kb.update_document(
        first["document_id"], content="失败新版本", expected_version=1
    )
    with pytest.raises(KnowledgeConflict):
        kb.update_document(first["document_id"], content="迟到编辑", expected_version=1)
    kb._embedding_service.fail = True
    execute(kb, second)
    detail = kb.document_detail(first["document_id"])
    assert detail["status"] == "failed" and detail["published_version"] == 1
    _, chunks = kb._repository.snapshot()
    assert {c["version"] for c in chunks} == {1}
    with pytest.raises(KnowledgeConflict, match="index_not_ready"):
        kb.set_document_published(first["document_id"])
    kb._embedding_service.fail = False
    execute(kb, second)
    assert kb.document_detail(first["document_id"])["published_version"] == 2


def test_source_chunks_cover_full_original_and_preserve_positions(kb):
    from app.services.knowledge_base.lifecycle import source_chunks

    original = (
        "# 条款\n\n| 型号 | 单位 | 期限 |\n| X | 天 | 7 |\n"
        + "长句无标点" * 180
        + "\n例外：定制商品不能退。"
    )
    chunks = source_chunks(original)
    covered = set()
    for c in chunks:
        assert (
            c["content"] == original[c["start_offset"] : c["end_offset"]]
            and len(c["content"]) <= 400
        )
        covered.update(range(c["start_offset"], c["end_offset"]))
    assert covered == set(range(len(original))) and any(
        "不能退" in c["content"] for c in chunks
    )


def test_repeated_index_is_same_version_same_chunks_and_true_batch(kb):
    result = kb.add_document("长文", "内容" * 2000)
    execute(kb, result)
    execute(kb, result)
    assert (
        len(kb._embedding_service.calls) == 1
        and len(kb._embedding_service.calls[0]) > 1
    )
    with kb._repository.transaction() as db:
        chunks = db.scalars(select(KnowledgeChunk)).all()
        assert len(chunks) == len(kb._embedding_service.calls[0])
        assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_index_write_partial_failure_and_expired_fence_never_publish(kb):
    result = kb.add_document("原发布", "原发布内容")
    execute(kb, result)
    updated = kb.update_document(
        result["document_id"], content="新内容", expected_version=1
    )

    class InterruptedIndex:
        def write(self, *args):
            with kb._repository.transaction() as db:
                job = db.get(BackgroundJob, updated["job_id"])
                job.lease_token = "replacement-owner"
                job.lease_expires_at = datetime.utcnow() + timedelta(seconds=60)

    kb._vector_store = InterruptedIndex()
    execute(kb, updated)
    assert kb.document_detail(result["document_id"])["published_version"] == 1
    with kb._repository.transaction() as db:
        assert (
            db.get(BackgroundJob, updated["job_id"]).lease_token == "replacement-owner"
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(KnowledgeChunk.version == 2)
            )
            == 0
        )


def test_source_context_keeps_faq_question_table_units_and_exception_tail():
    from app.services.knowledge_base.lifecycle import source_chunks

    faq = "## 售后\nQ: 定制商品能退吗？\nA: " + "说明文字" * 180 + "但定制商品不适用。"
    chunks = source_chunks(faq)
    assert any(
        c["source_context"]["faq_question"] == "Q: 定制商品能退吗？"
        and "不适用" in c["content"]
        for c in chunks
    )
    table = (
        "## 费用\n| 项目 | 金额（元） |\n| --- | --- |\n" + "| 合成项目 | 10 |\n" * 80
    )
    chunks = source_chunks(table)
    assert chunks[-1]["source_context"]["table_header"].startswith(
        "| 项目 | 金额（元） |"
    )


def test_100_chunks_use_five_worker_batches_with_exact_counts(kb):
    result = kb.add_document("100 chunks", "X" * 35050)
    execute(kb, result)
    assert len(kb._embedding_service.calls) == 5
    assert all(len(batch) == 20 for batch in kb._embedding_service.calls)
    with kb._repository.transaction() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeChunk)) == 100


async def test_real_job_consumer_retry_and_exhaustion_converge(kb, monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.services import background_jobs as jobs
    from app.models.models import KnowledgeVersion

    url = kb._repository.engine.url.set(drivername="sqlite+aiosqlite").render_as_string(
        hide_password=False
    )
    engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.services.knowledge_base.get_knowledge_base", lambda: kb)
    try:
        result = kb.add_document("worker", "真实消费路径")
        assert await jobs.tick(factory, kind="knowledge_index")
        assert kb.document_detail(result["document_id"])["status"] == "published"
        updated = kb.update_document(
            result["document_id"], content="过期任务", expected_version=1
        )
        with kb._repository.transaction() as db:
            job = db.get(BackgroundJob, updated["job_id"])
            job.status = "running"
            job.attempts = job.max_attempts
            job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.get(KnowledgeVersion, updated["version_id"]).status = "indexing"
        assert await jobs.claim(factory, kind="knowledge_index") is None
        assert kb.document_detail(result["document_id"])["status"] == "failed"
        assert kb.document_detail(result["document_id"])["published_version"] == 1
    finally:
        await engine.dispose()


def test_unpublish_and_rebuild_do_not_reactivate_document(kb):
    result = kb.add_document("发布控制", "合成条款")
    execute(kb, result)
    assert kb.set_document_published(result["document_id"], False)
    assert kb.list_documents()[0]["status"] == "archived"
    rebuilt = kb.rebuild_index()["results"][0]
    execute(kb, rebuilt)
    assert kb.document_detail(result["document_id"])["published_version"] is None
    assert kb._repository.snapshot()[1] == []


def test_configured_milvus_outage_cannot_publish_new_version(kb, monkeypatch):
    from app.config.config import settings

    first = kb.add_document("旧版", "原发布合成条款")
    execute(kb, first)
    updated = kb.update_document(
        first["document_id"], content="不得发布新条款", expected_version=1
    )
    monkeypatch.setattr(settings, "USE_MILVUS", True)
    execute(kb, updated)
    detail = kb.document_detail(first["document_id"])
    assert detail["status"] == "failed" and detail["published_version"] == 1


def test_unpublish_during_index_and_later_edit_do_not_implicitly_publish(kb):
    first = kb.add_document("发布控制", "原合成正文")
    execute(kb, first)
    next = kb.update_document(
        first["document_id"], content="索引中的正文", expected_version=1
    )

    class UnpublishDuringIndex:
        def write(self, *args):
            kb.set_document_published(first["document_id"], False)

    kb._vector_store = UnpublishDuringIndex()
    execute(kb, next)
    assert kb.document_detail(first["document_id"])["published_version"] is None
    kb._vector_store = None
    later = kb.update_document(
        first["document_id"], content="未发布文档后续编辑", expected_version=2
    )
    execute(kb, later)
    assert kb.document_detail(first["document_id"])["published_version"] is None


def test_sqlite_legacy_migration_preserves_text_and_legacy_chunk(tmp_path, monkeypatch):
    from sqlalchemy import text
    from alembic import command
    from alembic.config import Config
    from app.config.config import settings

    repo = KnowledgeRepository("sqlite:///" + (tmp_path / "legacy.db").as_posix())
    with repo.engine.begin() as db:
        db.execute(
            text(
                "CREATE TABLE knowledge_docs (id INTEGER PRIMARY KEY, title VARCHAR(200) NOT NULL,category VARCHAR(50) NOT NULL,content TEXT NOT NULL,chunk_count INTEGER,version INTEGER,is_published BOOLEAN,keywords JSON,source VARCHAR(500),deleted_at DATETIME,created_at DATETIME,updated_at DATETIME)"
            )
        )
        db.execute(
            text(
                "CREATE TABLE knowledge_chunks (id INTEGER PRIMARY KEY,document_id INTEGER NOT NULL,chunk_id VARCHAR(100) UNIQUE,content TEXT NOT NULL,embedding JSON,chunk_index INTEGER,version INTEGER,is_active BOOLEAN,created_at DATETIME)"
            )
        )
        db.execute(
            text(
                "INSERT INTO knowledge_docs VALUES(41,'legacy','policy','ZXKEY original',1,1,1,'[]','synthetic',NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.execute(
            text(
                "INSERT INTO knowledge_chunks VALUES(1,41,'legacy-c41','ZXKEY original','[1,0,0]',0,1,1,CURRENT_TIMESTAMP)"
            )
        )
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    monkeypatch.setattr(
        settings,
        "DATABASE_URL_OVERRIDE",
        repo.engine.url.set(drivername="sqlite+aiosqlite").render_as_string(
            hide_password=False
        ),
    )
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.stamp(cfg, "0008_m2_runs_jobs")
    command.upgrade(cfg, "head")
    detail = repo.detail("doc_db_41", 1)
    assert (
        detail["content"] == "ZXKEY original"
        and detail["status"] == "legacy_unverified"
    )
    assert repo.snapshot()[1][0]["id"] == "legacy-c41"
    assert repo.snapshot()[1][0]["embedding_fingerprint"] is None
    repo.engine.dispose()
