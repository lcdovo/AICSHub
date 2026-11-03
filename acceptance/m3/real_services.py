"""M3真实MySQL/Milvus与实际进程，只允许专用入口/合成库。"""

import json, os, uuid, multiprocessing
from pathlib import Path
from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine, text, select, func
from app.config.config import settings
from app.models.models import (
    Base,
    BackgroundJob,
    KnowledgeDoc,
    KnowledgeChunk,
    KnowledgeVersion,
    KnowledgeEpoch,
)
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.knowledge_base import KnowledgeBaseService
from app.services.knowledge_base.vector_index import VersionedVectorIndex
from app.services.knowledge_base.index_worker import index_job


class SyntheticEmbedding:
    _dim = 3
    backend_name = "synthetic-m3"

    def encode_batch(self, texts):
        return [
            [0.0, 1.0, 0.0] if "ZXKEY" in text else [1.0, 0.0, 0.0] for text in texts
        ]

    def encode_query(self, text):
        return [1.0, 0.0, 0.0]


@pytest.fixture
def real_case():
    assert os.environ.get("M3_TEST_STATE")
    name = "aicshub_m3_case_" + uuid.uuid4().hex[:12]
    base = settings.DATABASE_URL.replace("+aiomysql", "+pymysql")
    admin = create_engine(base)
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as db:
        db.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4"))
    admin.dispose()
    url = base.rsplit("/", 1)[0] + "/" + name
    repo = KnowledgeRepository(url)
    Base.metadata.create_all(repo.engine)
    with repo.transaction() as db:
        db.add(KnowledgeEpoch(id=1, value=0))
    index = VersionedVectorIndex(
        "127.0.0.1",
        settings.MILVUS_PORT,
        prefix="aicshub_m3_test_" + uuid.uuid4().hex[:12],
    )
    kb = KnowledgeBaseService(
        repository=repo, embedding=SyntheticEmbedding(), vector_store=index
    )
    kb.initialize()
    yield kb
    repo.engine.dispose()


def execute(kb, result):
    with kb._repository.transaction() as db:
        job = db.get(BackgroundJob, result["job_id"])
        job.status = "running"
        job.lease_token = "test-owner"
        job.lease_expires_at = datetime.utcnow() + timedelta(seconds=60)
        job.attempts += 1
    index_job(
        kb._repository,
        kb._embedding_service,
        kb._vector_store,
        result["job_id"],
        "test-owner",
    )
    detail = kb.document_detail(result["document_id"], result["version"])
    assert detail["status"] in {"published", "ready"}, detail["error_code"]


def test_real_milvus_dense_and_keyword_unique_hits(real_case):
    kb = real_case
    a = kb.add_document("词路独有", "ZXKEY unique keyword clause")
    execute(kb, a)
    b = kb.add_document("向量独有", "semantic other candidate")
    execute(kb, b)
    result = kb.search("ZXKEY", top_k=2, similarity_threshold=0.8)
    routes = result["meta"]["routes"]
    assert routes["dense"]["backend"] == "milvus"
    assert len(routes["dense"]["ids"]) == len(routes["bm25"]["ids"]) == 1
    assert set(routes["dense"]["ids"]).isdisjoint(routes["bm25"]["ids"])
    assert {r["document_id"] for r in result["results"]} == {
        a["document_id"],
        b["document_id"],
    }
    path = (
        Path(os.environ["M3_TEST_STATE"]).parents[1] / "milvus-schema-and-routes.json"
    )
    from app.services.knowledge_base.lifecycle import embedding_identity

    fingerprint, _ = embedding_identity(kb._embedding_service)
    schema = kb._vector_store.client.describe_collection(
        kb._vector_store.collection(fingerprint)
    )
    indices = kb._vector_store.client.list_indexes(
        kb._vector_store.collection(fingerprint)
    )
    path.write_text(
        json.dumps(
            {
                "schema": schema,
                "indices": indices,
                "routes": routes,
                "search_strategy": result["search_strategy"],
                "embedding": "synthetic vectors, not model output",
                "keyword_backend": "SQL snapshot application BM25",
            },
            default=str,
        ),
        encoding="utf-8",
    )


def test_mysql_same_title_update_delete_restart_and_failed_index(real_case):
    kb = real_case
    a = kb.add_document("同名", "ZXKEY first")
    execute(kb, a)
    b = kb.add_document("同名", "ZXKEY second")
    execute(kb, b)
    old = kb.search("ZXKEY")
    original = kb._vector_store.write

    def fail(*args):
        raise RuntimeError("synthetic-index-outage")

    kb._vector_store.write = fail
    next = kb.update_document(
        a["document_id"], content="ZXKEY changed", expected_version=1
    )
    with kb._repository.transaction() as db:
        j = db.get(BackgroundJob, next["job_id"])
        j.status = "running"
        j.lease_token = "test-owner"
        j.lease_expires_at = datetime.utcnow() + timedelta(seconds=60)
    index_job(
        kb._repository,
        kb._embedding_service,
        kb._vector_store,
        next["job_id"],
        "test-owner",
    )
    assert kb.document_detail(a["document_id"])["published_version"] == 1
    assert all(r["version"] == 1 for r in kb.search("ZXKEY")["results"])
    kb._vector_store.write = original
    execute(kb, next)
    restarted = KnowledgeBaseService(
        repository=kb._repository,
        embedding=SyntheticEmbedding(),
        vector_store=kb._vector_store,
    )
    assert restarted.document_detail(a["document_id"], 1)["content"] == "ZXKEY first"
    assert not restarted.evidence_is_current(old["results"])
    assert restarted.delete_document(a["document_id"])
    result = restarted.search("ZXKEY")
    assert {r["document_id"] for r in result["results"]} == {b["document_id"]}
    assert kb.document_detail(b["document_id"])["published_version"] == 1


def editor_worker(url, sid, barrier, queue, index):
    from app.repositories.knowledge_repository import (
        KnowledgeRepository,
        KnowledgeConflict,
    )

    repo = KnowledgeRepository(url)
    try:
        for i in range(20):
            barrier.wait(30)
            try:
                result = repo.save(
                    document_id=sid,
                    expected_version=i + 1,
                    title="竞争",
                    content=f"ZXKEY editor{index}-{i}",
                )
                queue.put(("saved", result))
            except KnowledgeConflict:
                queue.put(("conflict", None))
            barrier.wait(30)
    finally:
        repo.engine.dispose()


def test_two_actual_editors_cas_and_two_readers_20_rounds(real_case):
    kb = real_case
    first = kb.add_document("竞争", "ZXKEY original")
    execute(kb, first)
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(3)
    queue = ctx.Queue()
    url = kb._repository.engine.url.render_as_string(hide_password=False)
    workers = [
        ctx.Process(
            target=editor_worker, args=(url, first["document_id"], barrier, queue, i)
        )
        for i in range(2)
    ]
    readers = [
        KnowledgeBaseService(
            repository=kb._repository,
            embedding=SyntheticEmbedding(),
            vector_store=kb._vector_store,
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for i in range(20):
        for reader in readers:
            assert {r["version"] for r in reader.search("ZXKEY")["results"]} == {i + 1}
        barrier.wait(30)
        out = [queue.get(timeout=30) for _ in range(2)]
        assert sorted(x[0] for x in out) == ["conflict", "saved"]
        winner = next(x[1] for x in out if x[0] == "saved")
        execute(kb, winner)
        for reader in readers:
            result = reader.search("ZXKEY")
            assert {r["version"] for r in result["results"]} == {i + 2}
        barrier.wait(30)
    for worker in workers:
        worker.join(10)
        assert worker.exitcode == 0


def crash_index_worker(url, jid, point, index_prefix):
    repo = KnowledgeRepository(url)
    index = VersionedVectorIndex("127.0.0.1", settings.MILVUS_PORT, prefix=index_prefix)
    if point == "after_index":
        original = index.write

        def write(*args):
            original(*args)
            os._exit(26)

        index.write = write
    else:
        os._exit(26)
    index_job(repo, SyntheticEmbedding(), index, jid, "test-owner")


@pytest.mark.parametrize("point", ["before_index", "after_index"])
def test_actual_index_worker_crash_resumes_same_version(real_case, point):
    kb = real_case
    result = kb.add_document("恢复", "ZXKEY recovery clause")
    with kb._repository.transaction() as db:
        job = db.get(BackgroundJob, result["job_id"])
        job.status = "running"
        job.lease_token = "test-owner"
        job.lease_expires_at = datetime.utcnow() + timedelta(seconds=60)
    ctx = multiprocessing.get_context("spawn")
    worker = ctx.Process(
        target=crash_index_worker,
        args=(
            kb._repository.engine.url.render_as_string(hide_password=False),
            result["job_id"],
            point,
            kb._vector_store.prefix,
        ),
    )
    worker.start()
    worker.join(60)
    assert worker.exitcode == 26
    assert kb._repository.snapshot()[1] == []
    execute(kb, result)
    execute(kb, result)
    with kb._repository.transaction() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeVersion)) == 1
        assert db.scalar(select(func.count()).select_from(KnowledgeChunk)) == 1
    assert len(kb.search("ZXKEY")["results"]) == 1


def test_mysql_legacy_migration_keeps_original_ids_content_and_keyword_fallback(
    real_case, monkeypatch
):
    from alembic import command
    from alembic.config import Config

    kb = real_case
    repo = kb._repository
    with repo.transaction() as db:
        old = KnowledgeDoc(
            title="同名历史",
            content="ZXKEY legacy original",
            category="policy",
            version=1,
            is_published=True,
        )
        db.add(old)
        db.flush()
        did = old.id
        db.add(
            KnowledgeChunk(
                document_id=did,
                chunk_id="legacy-stable-chunk",
                content="ZXKEY legacy original",
                embedding=[1.0, 0.0, 0.0],
                version=1,
            )
        )
    assert repo.engine.url.database.startswith("aicshub_m3_case_")
    with repo.engine.begin() as db:
        db.execute(text("DROP TABLE knowledge_versions"))
        db.execute(text("DROP TABLE knowledge_epoch"))
        db.execute(
            text(
                "ALTER TABLE knowledge_docs DROP COLUMN stable_id,DROP COLUMN published_version,DROP COLUMN lifecycle_status"
            )
        )
        db.execute(
            text(
                "ALTER TABLE knowledge_chunks DROP COLUMN content_hash,DROP COLUMN start_offset,DROP COLUMN end_offset,DROP COLUMN section,DROP COLUMN chunk_policy,DROP COLUMN source_context"
            )
        )
    monkeypatch.setattr(
        settings,
        "DATABASE_URL_OVERRIDE",
        repo.engine.url.set(drivername="mysql+aiomysql").render_as_string(
            hide_password=False
        ),
    )
    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.stamp(cfg, "0008_m2_runs_jobs")
    command.upgrade(cfg, "head")
    detail = repo.detail("doc_mysql_" + str(did), 1)
    assert (
        detail["content"] == "ZXKEY legacy original"
        and detail["status"] == "legacy_unverified"
    )
    assert repo.detail("doc_db_" + str(did), 1)["document_id"] == detail["document_id"]
    result = kb.search("ZXKEY")
    assert result["results"] and result["meta"]["routes"]["dense"]["ids"] == []
    assert "embedding_fingerprint_mismatch" in result["degraded_reason"]
    execute(kb, kb.update_document(detail["document_id"], expected_version=1))
    assert (
        kb.document_detail(detail["document_id"], 1)["content"]
        == "ZXKEY legacy original"
    )


def deletion_reader(url, index_prefix, sid, ready, proceed, queue):
    repo = KnowledgeRepository(url)
    index = VersionedVectorIndex("127.0.0.1", settings.MILVUS_PORT, prefix=index_prefix)
    reader = KnowledgeBaseService(
        repository=repo, embedding=SyntheticEmbedding(), vector_store=index
    )
    result = reader.search("ZXKEY")
    assert result["results"]
    ready.set()
    assert proceed.wait(30)
    result = reader.search("ZXKEY")
    queue.put([r["document_id"] for r in result["results"]])
    repo.engine.dispose()


def test_two_actual_cached_readers_exclude_deleted_document(real_case):
    kb = real_case
    original = kb.add_document("删除", "ZXKEY synthetic deletion")
    execute(kb, original)
    ctx = multiprocessing.get_context("spawn")
    ready = [ctx.Event(), ctx.Event()]
    proceed = ctx.Event()
    queue = ctx.Queue()
    workers = [
        ctx.Process(
            target=deletion_reader,
            args=(
                kb._repository.engine.url.render_as_string(hide_password=False),
                kb._vector_store.prefix,
                original["document_id"],
                event,
                proceed,
                queue,
            ),
        )
        for event in ready
    ]
    for worker in workers:
        worker.start()
    for event in ready:
        assert event.wait(30)
    kb.delete_document(original["document_id"])
    proceed.set()
    assert [queue.get(timeout=30) for _ in workers] == [[], []]
    for worker in workers:
        worker.join(10)
        assert worker.exitcode == 0


def test_sql_snapshot_remains_consistent_during_publish(real_case):
    from sqlalchemy import event
    from concurrent.futures import ThreadPoolExecutor
    import threading

    kb = real_case
    first = kb.add_document("快照", "ZXKEY old snapshot")
    execute(kb, first)
    second = kb.update_document(
        first["document_id"],
        content="ZXKEY new snapshot",
        expected_version=1,
        is_published=False,
    )
    execute(kb, second)
    entered, release = threading.Event(), threading.Event()

    def pause(conn, cursor, statement, params, context, many):
        if (
            threading.current_thread().name.startswith("m3-snapshot-test")
            and "knowledge_epoch" in statement
        ):
            entered.set()
            assert release.wait(20)

    event.listen(kb._repository.engine, "after_cursor_execute", pause)
    try:
        with ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="m3-snapshot-test"
        ) as pool:
            old = pool.submit(kb._repository.snapshot)
            assert entered.wait(20)
            kb.set_document_published(
                first["document_id"], True, version=2, expected_version=2
            )
            release.set()
            epoch, chunks = old.result(timeout=20)
            assert {c["version"] for c in chunks} == {1}
        assert {c["version"] for c in kb._repository.snapshot()[1]} == {2}
    finally:
        release.set()
        event.remove(kb._repository.engine, "after_cursor_execute", pause)
