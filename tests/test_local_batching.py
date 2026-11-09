"""跨请求批处理的归属、容量、失效与在途释放边界；全部合成HTTP。"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import threading
import time
from unittest.mock import Mock

import httpx
import pytest

from app.config.config import settings
from app.services import local_admission as admission
from app.services.embedding_service import APIEmbedding
from app.services.local_batching import EmbeddingBatcher, ModelLane
from app.services.local_inference import LocalModelBusy, LocalModelDeadline, inference_scope
from app.services.reranker_service import RerankerService


@pytest.fixture
def batch_mode(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_MODEL_COORDINATOR", "batch")
    monkeypatch.setattr(settings, "WORKERS", 1)
    coordinator = admission.BatchCoordinator()
    monkeypatch.setattr(admission, "BATCH", coordinator)
    monkeypatch.setattr(admission, "RERANK_LANE", ModelLane(2))
    monkeypatch.setattr(admission, "EMBEDDING_LANE", ModelLane(1))
    return coordinator


def collect_together(monkeypatch, batcher, count):
    original = batcher._execute

    def execute():
        with batcher._condition:
            assert batcher._condition.wait_for(lambda: len(batcher._queue) >= count, timeout=2)
        original()

    monkeypatch.setattr(batcher, "_execute", execute)


def test_ten_request_pipeline_batches_embeddings_and_preserves_candidate_owners(batch_mode, monkeypatch):
    import json
    calls = []

    def respond(request):
        payload = json.loads(request.content)
        calls.append((request.url.path, payload))
        if request.url.path.endswith("embeddings"):
            data = [{"index": i, "embedding": [float(text), 1, 0]}
                    for i, text in enumerate(payload["input"])]
            return httpx.Response(200, json={"data": list(reversed(data))})
        assert len(payload["documents"]) == 20
        assert all(t.startswith(payload["query"]+":") for t in payload["documents"])
        return httpx.Response(200, json={"results": [
            {"index": i, "relevance_score": i/20} for i in reversed(range(20))]})

    embedding = APIEmbedding("http://127.0.0.1:8013/v1", "synthetic", "synthetic", dim=3, max_retries=0)
    reranker = RerankerService(local_base="http://127.0.0.1:7999", local_model="synthetic")
    embedding._client = httpx.Client(transport=httpx.MockTransport(respond))
    reranker._providers[0]._client = httpx.Client(transport=httpx.MockTransport(respond))
    collect_together(monkeypatch, embedding._batcher, 10)

    def run(owner):
        events = []
        with inference_scope(events=events), admission.request_scope():
            assert embedding.encode_query(str(owner)) == [float(owner), 1, 0]
            candidates = [{"id": f"{owner}:{i}", "content": f"{owner}:{i}"} for i in range(20)]
            result = reranker.rerank_sync(str(owner), candidates, 20, deadline=time.monotonic()+5)
            assert [r["id"] for r in result] == [f"{owner}:{i}" for i in reversed(range(20))]
        return events

    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            rows = list(pool.map(run, range(10)))
        assert len([c for c in calls if c[0].endswith("embeddings")]) == 1
        assert len([c for c in calls if c[0].endswith("rerank")]) == 10
        assert all(next(e for e in events if e["purpose"] == "embedding_batch")["requests"] == 10 for events in rows)
        assert not batch_mode.owners
        assert embedding._batcher._outstanding == 0
    finally:
        reranker.close()
        embedding.close()


def test_capacity_rejects_before_http_and_releasing_one_owner_keeps_others(batch_mode):
    owners = [batch_mode.acquire(priority="interactive", deadline=time.monotonic()+5) for _ in range(10)]
    batch_mode.max_waiters = 0
    call = Mock()
    try:
        with pytest.raises(LocalModelBusy):
            admission.run_http(call, purpose="embedding")
        call.assert_not_called()
        owners[0].close()
        replacement = batch_mode.acquire(priority="interactive", deadline=time.monotonic()+5)
        owners[0].close()
        assert batch_mode.valid(replacement.token)
        assert all(batch_mode.valid(p.token) for p in owners[1:])
        replacement.close()
    finally:
        for owner in owners:
            owner.close()


def test_multiworker_batch_fails_closed(batch_mode, monkeypatch):
    monkeypatch.setattr(settings, "WORKERS", 2)
    call = Mock()
    with pytest.raises(LocalModelBusy, match="single_worker"):
        admission.run_http(call, purpose="embedding")
    call.assert_not_called()


def test_rerank_lane_has_two_active_calls_and_expired_waiter_never_dispatches(batch_mode):
    entered = threading.Barrier(3)
    release = threading.Event()
    call = Mock()

    def holding():
        return admission.run_http(lambda _: (entered.wait(2), release.wait(2), "ok")[-1], purpose="rerank")

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(holding) for _ in range(2)]
        entered.wait(2)
        try:
            with inference_scope(deadline=time.monotonic()+.02), pytest.raises(LocalModelDeadline):
                admission.run_http(call, purpose="rerank")
            call.assert_not_called()
        finally:
            release.set()
        assert [f.result() for f in pending] == ["ok", "ok"]


def test_inflight_batch_retains_owner_until_http_returns(batch_mode):
    entered, release = threading.Event(), threading.Event()
    batcher = EmbeddingBatcher(lambda texts, _, guard: (guard(), entered.set(), release.wait(2), list(texts))[-1])
    with ThreadPoolExecutor(max_workers=1) as pool:
        with admission.request_scope() as permit:
            def infer():
                return admission.run_http(lambda remaining: batcher.submit(("private",),
                    deadline=time.monotonic()+remaining, guard=permit.check), purpose="embedding")
            pending = pool.submit(copy_context().run, infer)
            assert entered.wait(1)
        assert batch_mode.valid(permit.token)
        release.set()
        with pytest.raises(LocalModelDeadline):
            pending.result()
    assert not batch_mode.valid(permit.token)


def test_lost_request_does_not_publish_or_cancel_other_batch_member(monkeypatch):
    lost = threading.Event()

    def infer(texts, deadline, guard):
        guard()
        lost.set()
        return list(texts)

    batcher = EmbeddingBatcher(infer)
    collect_together(monkeypatch, batcher, 2)

    def run(text):
        def guard():
            if text == "lost" and lost.is_set():
                raise LocalModelDeadline("lost")
        return batcher.submit((text,), deadline=time.monotonic()+2, guard=guard)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(run, "lost"), pool.submit(run, "valid")
        with pytest.raises(LocalModelDeadline):
            first.result()
        assert second.result() == ["valid"]
    assert batcher._outstanding == 0


@pytest.mark.parametrize("invalid", [[], [1, 2, 3], None])
def test_partial_batch_fails_all_without_routing_wrong_owner(monkeypatch, invalid):
    batcher = EmbeddingBatcher(lambda *_: invalid)
    collect_together(monkeypatch, batcher, 2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(batcher.submit, (str(i),), deadline=time.monotonic()+2, guard=lambda: None)
                   for i in range(2)]
        for future in pending:
            with pytest.raises(ValueError, match="response_count"):
                future.result()


def test_queued_deadline_and_close_never_dispatch_queued_text():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def infer(texts, _, guard):
        guard()
        calls.append(texts)
        entered.set()
        assert release.wait(2)
        return list(texts)

    batcher = EmbeddingBatcher(infer, max_requests=2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(batcher.submit, ("active",), deadline=time.monotonic()+2, guard=lambda: None)
        assert entered.wait(1)
        with pytest.raises(LocalModelDeadline):
            batcher.submit(("expired",), deadline=time.monotonic()+.01, guard=lambda: None)
        second = pool.submit(batcher.submit, ("closed",), deadline=time.monotonic()+2, guard=lambda: None)
        with batcher._condition:
            assert batcher._condition.wait_for(lambda: len(batcher._queue) == 1, timeout=1)
        batcher.close()
        with pytest.raises(LocalModelDeadline):
            second.result()
        release.set()
        assert first.result() == ["active"]
    assert calls == [["active"]]
    assert batcher._outstanding == 0


def test_batch_request_and_item_limits_are_bounded():
    infer = Mock()
    batcher = EmbeddingBatcher(infer, max_items=2)
    with pytest.raises(ValueError):
        batcher.submit(("1", "2", "3"), deadline=time.monotonic()+1, guard=lambda: None)
    with pytest.raises(LocalModelDeadline):
        batcher.submit(("expired",), deadline=time.monotonic()-1, guard=lambda: None)
    infer.assert_not_called()


def test_background_has_one_whole_request_slot(batch_mode):
    owner = batch_mode.acquire(priority="background", deadline=time.monotonic()+1)
    try:
        with pytest.raises(LocalModelBusy):
            batch_mode.acquire(priority="background", deadline=time.monotonic()+1)
        other = batch_mode.acquire(priority="interactive", deadline=time.monotonic()+1)
        assert batch_mode.valid(other.token)
        other.close()
    finally:
        owner.close()


def test_interactive_stays_ahead_after_four_background_fairness_admissions():
    coordinator = admission.BatchCoordinator(capacity=1)
    coordinator._interactive_streak = 4
    owner = coordinator.acquire(priority="interactive", deadline=time.monotonic()+1)
    order = []

    def run(priority):
        permit = coordinator.acquire(priority=priority, deadline=time.monotonic()+1)
        order.append(priority)
        permit.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        background = pool.submit(run, "background")
        interactive = pool.submit(run, "interactive")
        with coordinator.condition:
            assert coordinator.condition.wait_for(lambda: sum(map(len, coordinator.waiters.values())) == 2, timeout=1)
        owner.close()
        background.result()
        interactive.result()
    assert order == ["background", "interactive"]


def test_loss_while_waiting_for_embedding_lane_prevents_http(batch_mode):
    lane = admission.EMBEDDING_LANE
    entered, release = threading.Event(), threading.Event()
    embedding = APIEmbedding("http://127.0.0.1/v1", "synthetic", "synthetic", dim=3, max_retries=0)
    client = Mock(is_closed=False)
    embedding._client = client
    live = [True]

    def guard():
        if not live[0]:
            raise LocalModelDeadline("lost_before_http")

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            holder = pool.submit(lane.run, lambda _: (entered.set(), release.wait(2)), 2)
            assert entered.wait(1)
            pending = pool.submit(embedding._infer_shared_batch, ["private"], time.monotonic()+2, guard)
            with lane._condition:
                assert lane._condition.wait_for(lambda: len(lane._queues["interactive"]) == 1, timeout=1)
            live[0] = False
            release.set()
            holder.result()
            with pytest.raises(LocalModelDeadline, match="lost_before_http"):
                pending.result()
        client.post.assert_not_called()
    finally:
        embedding.close()


def test_model_lane_fifo_and_background_fairness():
    lane = ModelLane(1)
    lane._interactive_streak = 4
    entered, release = threading.Event(), threading.Event()
    order = []

    def run(priority):
        with inference_scope(priority=priority):
            return lane.run(lambda _: order.append(priority), 1)

    with ThreadPoolExecutor(max_workers=3) as pool:
        holder = pool.submit(lane.run, lambda _: (entered.set(), release.wait(2)), 2)
        assert entered.wait(1)
        interactive = pool.submit(run, "interactive")
        background = pool.submit(run, "background")
        with lane._condition:
            assert lane._condition.wait_for(lambda: sum(map(len, lane._queues.values())) == 2, timeout=1)
        release.set()
        holder.result()
        interactive.result()
        background.result()
    assert order == ["background", "interactive"]


def test_large_background_input_is_split_without_dropping_tail(batch_mode):
    embedding = APIEmbedding("http://127.0.0.1/v1", "synthetic", "synthetic", dim=3, batch_size=100)
    try:
        texts = [str(i) for i in range(45)]
        batches = embedding._iter_batches(texts)
        assert list(map(len, batches)) == [20, 20, 5]
        assert [text for batch in batches for text in batch] == texts
    finally:
        embedding.close()


@pytest.mark.parametrize("data", [
    [{"index": 0, "embedding": [1, 2, 3]}, {"index": 0, "embedding": [4, 5, 6]}],
    [{"index": 0, "embedding": [1, 2]}, {"index": 1, "embedding": [4, 5, 6]}],
    [{"index": 0, "embedding": [float("nan"), 2, 3]}, {"index": 1, "embedding": [4, 5, 6]}],
])
def test_batch_decoder_rejects_duplicate_wrong_dimension_and_nonfinite(batch_mode, data):
    embedding = APIEmbedding("http://127.0.0.1/v1", "synthetic", "synthetic", dim=3, max_retries=0)
    client = Mock(is_closed=False)
    client.post.return_value.raise_for_status.return_value.json.return_value = {"data": data}
    embedding._client = client
    try:
        with pytest.raises(ValueError):
            embedding._infer_shared_batch(["a", "b"], time.monotonic()+1)
    finally:
        embedding.close()
