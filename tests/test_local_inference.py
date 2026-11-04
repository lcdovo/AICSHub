from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from unittest.mock import Mock

import httpx
import pytest

from app.services.local_inference import (
    LocalInferenceScheduler, LocalModelBusy, LocalModelDeadline, inference_scope,
)
from tests.test_m3_lifecycle import kb as kb


def wait_for_waiters(scheduler, count):
    end = time.monotonic() + 2
    while time.monotonic() < end:
        with scheduler._condition:
            if len(scheduler._interactive) + len(scheduler._background) == count:
                return
        time.sleep(.001)
    raise AssertionError("queue did not reach expected size")


def test_one_execution_two_waiters_and_explicit_busy_without_dispatch():
    scheduler = LocalInferenceScheduler(max_waiters=2, queue_seconds=2)
    entered, release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=4) as pool:
        owner = pool.submit(scheduler.run, lambda _: (entered.set(), release.wait(2))[1], purpose="embedding")
        assert entered.wait(1)
        first = pool.submit(scheduler.run, lambda _: "one", purpose="rerank")
        second = pool.submit(scheduler.run, lambda _: "two", purpose="embedding")
        try:
            wait_for_waiters(scheduler, 2)
            rejected = Mock()
            with pytest.raises(LocalModelBusy, match="queue_full"):
                scheduler.run(rejected, purpose="embedding")
            rejected.assert_not_called()
        finally:
            release.set()
        assert owner.result() and first.result() == "one" and second.result() == "two"


def test_expired_or_waiting_request_never_dispatches_and_does_not_leave_queue_entry():
    scheduler = LocalInferenceScheduler(queue_seconds=.01)
    call = Mock()
    with pytest.raises(LocalModelDeadline):
        scheduler.run(call, purpose="embedding", deadline=time.monotonic() - 1)
    call.assert_not_called()
    scheduler._active = True
    with pytest.raises(LocalModelBusy):
        scheduler.run(call, purpose="embedding")
    assert not scheduler._interactive
    scheduler._active = False
    assert scheduler.run(lambda _: "ok", purpose="embedding") == "ok"


def test_late_result_is_rejected_and_slot_not_released_until_actual_call_finishes():
    scheduler = LocalInferenceScheduler(max_waiters=0, phase_seconds=.02)
    entered, release = threading.Event(), threading.Event()
    def slow(_):
        entered.set()
        release.wait(2)
        return "late"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(scheduler.run, slow, purpose="embedding")
        assert entered.wait(1)
        time.sleep(.03)
        try:
            with pytest.raises(LocalModelBusy):
                scheduler.run(lambda _: "must-not-run", purpose="rerank")
        finally:
            release.set()
        with pytest.raises(LocalModelDeadline):
            future.result()
    assert not scheduler._active


def test_interactive_priority_precedes_background_waiter():
    scheduler = LocalInferenceScheduler(queue_seconds=1)
    scheduler._active = True
    seen = []
    def background():
        with inference_scope(priority="background"):
            return scheduler.run(lambda _: seen.append("background"), purpose="embedding")
    with ThreadPoolExecutor(max_workers=2) as pool:
        bg = pool.submit(background)
        wait_for_waiters(scheduler, 1)
        fg = pool.submit(scheduler.run, lambda _: seen.append("interactive"), purpose="rerank")
        wait_for_waiters(scheduler, 2)
        with scheduler._condition:
            scheduler._active = False
            scheduler._condition.notify_all()
        bg.result()
        fg.result()
    assert seen == ["interactive", "background"]


def test_observation_separates_queue_inference_rejection_and_contains_no_payload():
    scheduler = LocalInferenceScheduler(max_waiters=0)
    observations = []
    with inference_scope(events=observations):
        assert scheduler.run(lambda _: "synthetic-private-value", purpose="embedding") == "synthetic-private-value"
        scheduler._active = True
        try:
            with pytest.raises(LocalModelBusy):
                scheduler.run(lambda _: None, purpose="rerank")
        finally:
            scheduler._active = False
    assert observations[0]["status"] == "succeeded" and observations[0]["inference_ms"] is not None
    assert observations[1]["status"] == "busy" and not observations[1]["dispatched"]
    assert observations[1]["inference_ms"] is None
    assert "private" not in json.dumps(observations)


def test_embedding_and_reranker_http_share_same_scheduler_and_busy_embedding_does_not_retry(monkeypatch):
    from app.services.embedding_service import APIEmbedding
    from app.services.reranker_service import _RerankProvider
    from app.services.local_admission import ProcessCoordinator
    scheduler = ProcessCoordinator(max_waiters=0)
    monkeypatch.setattr("app.services.local_admission.get_coordinator", lambda: scheduler)
    owner = scheduler.acquire(priority="interactive", deadline=time.monotonic()+5)
    embedding = APIEmbedding("http://127.0.0.1:8013/v1", "synthetic", "synthetic", dim=3)
    provider = _RerankProvider("local", "http://127.0.0.1:7999", "", "synthetic", "openai", 3)
    call = Mock(side_effect=AssertionError("busy request dispatched"))
    embedding._client = httpx.Client(transport=httpx.MockTransport(call))
    monkeypatch.setattr(provider, "_post_uncoordinated", call)
    try:
        with pytest.raises(LocalModelBusy):
            embedding.encode_query("synthetic")
        with pytest.raises(LocalModelBusy):
            provider._post("http://127.0.0.1:7999/rerank", {})
        call.assert_not_called()
    finally:
        owner.close()
        embedding.close()


def test_external_embedding_is_not_part_of_local_gpu_budget(monkeypatch):
    from app.services.embedding_service import APIEmbedding
    from app.services.local_admission import ProcessCoordinator
    scheduler = ProcessCoordinator(max_waiters=0)
    monkeypatch.setattr("app.services.local_admission.get_coordinator", lambda: scheduler)
    owner = scheduler.acquire(priority="interactive",deadline=time.monotonic()+5)
    embedding = APIEmbedding("https://synthetic.invalid/v1", "synthetic", "synthetic", dim=3)
    embedding._client = httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1., 0., 0.]}]})))
    try:
        assert embedding.encode_query("synthetic") == [1., 0., 0.]
    finally:
        owner.close()
        embedding.close()


def test_inference_exception_releases_slot_and_http_timeout_is_not_a_success():
    scheduler = LocalInferenceScheduler()
    events = []
    def timeout(_):
        raise httpx.ReadTimeout("synthetic-private-error")
    with inference_scope(events=events):
        with pytest.raises(LocalModelDeadline):
            scheduler.run(timeout, purpose="embedding")
        assert scheduler.run(lambda _: "next", purpose="rerank") == "next"
    assert [e["status"] for e in events] == ["deadline", "succeeded"]
    assert "private" not in json.dumps(events)


def test_foreground_scope_is_restored_after_background_job_context():
    from app.services.local_inference import CONTEXT
    assert CONTEXT.get().priority == "interactive"
    with inference_scope(priority="background", deadline=time.monotonic()+1):
        assert CONTEXT.get().priority == "background"
    assert CONTEXT.get().priority == "interactive" and CONTEXT.get().deadline is None


def test_retrieval_worker_carries_deadline_and_observations_context():
    from app.services.knowledge_base.versioned_search import bounded_provider
    scheduler = LocalInferenceScheduler()
    events = []
    with inference_scope(events=events):
        assert bounded_provider(lambda: scheduler.run(lambda _: "ok", purpose="embedding")) == "ok"
    assert len(events) == 1 and events[0]["dispatched"]


def test_background_busy_sets_bounded_retry_without_republishing_or_losing_current_version(tmp_path, monkeypatch):
    from app.models.models import Base, BackgroundJob
    from app.repositories.knowledge_repository import KnowledgeRepository
    from app.services.knowledge_base import KnowledgeBaseService
    from tests.test_m3_lifecycle import SyntheticEmbedding, execute
    from app.config.config import settings
    monkeypatch.setattr(settings, "USE_MILVUS", False)
    repo = KnowledgeRepository("sqlite:///" + (tmp_path / "busy.db").as_posix())
    Base.metadata.create_all(repo.engine)
    embedding = SyntheticEmbedding()
    kb = KnowledgeBaseService(repository=repo, embedding=embedding)
    try:
        original = kb.add_document("合成", "已发布原始政策")
        execute(kb, original)
        update = kb.update_document(original["document_id"], content="排队中的新政策", expected_version=1)
        monkeypatch.setattr(embedding, "encode_batch", Mock(side_effect=LocalModelBusy("synthetic_busy")))
        execute(kb, update)
        with repo.transaction() as db:
            job = db.get(BackgroundJob, update["job_id"])
            assert job.status == "retrying" and job.last_error == "local_model_busy"
            assert job.lease_token is None and job.next_run > job.updated_at
        detail = kb.document_detail(original["document_id"])
        assert detail["published_version"] == 1 and detail["status"] == "draft"
        with repo.transaction() as db:
            job = db.get(BackgroundJob, update["job_id"])
            job.attempts = job.max_attempts
        execute(kb, update)
        with repo.transaction() as db:
            job = db.get(BackgroundJob, update["job_id"])
            assert job.status == "failed" and job.last_error == "attempts_exhausted"
        assert kb.document_detail(original["document_id"])["published_version"] == 1
    finally:
        repo.engine.dispose()


@pytest.mark.parametrize("admission,expected", [("busy", "繁忙"), ("deadline", "超时")])
async def test_chat_retrieval_explains_busy_or_timeout_without_creating_handoff(admission, expected):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.agent.state import AgentState, AgentNode
    from app.agent.nodes.rag_node import RetrievalDependencies, retrieve_evidence
    result = {"success": True, "results": [], "meta": {"score_type": "rrf", "knowledge_epoch": 1,
              "local_model_admission": admission},
              "evidence_bundle": {"schema_version": 1, "knowledge_epoch": 1,
                                  "items": [], "support": "unknown"}}
    kb = SimpleNamespace(async_search=AsyncMock(return_value=result))
    state = AgentState(session_id="s", user_id=101, user_message="合成规则问题")
    assert await retrieve_evidence(RetrievalDependencies(lambda: kb, lambda: 3, .35), state) == AgentNode.RESPONSE_GENERATION
    assert expected in state.reply and "尚未创建" in state.reply
    assert not state.need_human and not state.pending_confirmations
    assert all(call.tool_name == "hybrid_search" for call in state.tool_calls)


@pytest.mark.parametrize("failure,admission", [(LocalModelBusy, "busy"), (LocalModelDeadline, "deadline"),
                                            (TimeoutError, "deadline")])
def test_transient_admission_failure_cannot_poison_retrieval_cache(kb, monkeypatch, failure, admission):
    from app.config.config import settings
    from tests.test_m3_lifecycle import execute
    monkeypatch.setattr(settings, "RAG_USE_RERANKER", False)
    execute(kb, kb.add_document("合成政策", "合成定制商品规则例外"))
    encode = kb._embedding_service.encode_query
    monkeypatch.setattr(kb._embedding_service, "encode_query", Mock(side_effect=failure("synthetic")))
    busy = kb.search("合成定制")
    assert busy["degraded"]
    monkeypatch.setattr(kb._embedding_service, "encode_query", encode)
    recovered = kb.search("合成定制")
    assert not recovered["from_cache"] and recovered["meta"]["routes"]["dense"]["ids"]
    assert busy["meta"]["local_model_admission"] == admission
    cached = kb.search("合成定制")
    assert cached["from_cache"] and cached["meta"]["local_model_admission"] == "cache"
    assert cached["meta"]["local_inference"] == []
