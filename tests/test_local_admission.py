from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import threading
import time

import pytest

from app.services.local_admission import ProcessCoordinator, RedisCoordinator, request_scope, run_http
from app.services.local_inference import LocalModelBusy, LocalModelDeadline, inference_scope


@pytest.fixture
def coordinator(monkeypatch):
    coord = ProcessCoordinator()
    monkeypatch.setattr("app.services.local_admission.get_coordinator", lambda: coord)
    return coord


def test_whole_turn_does_not_requeue_between_embedding_and_reranking(coordinator):
    events = []
    with inference_scope(events=events):
        with request_scope() as permit:
            assert run_http(lambda _: "embedding",purpose="embedding") == "embedding"
            assert run_http(lambda _: "rerank",purpose="rerank") == "rerank"
            assert coordinator.owner == permit.token
    assert coordinator.owner is None
    assert [e["purpose"] for e in events] == ["embedding","rerank","retrieval"]
    assert events[-1]["status"] == "admitted"


def test_busy_rejects_before_either_model_can_dispatch(coordinator):
    coord = coordinator
    coord.max_waiters = 0
    owner = coord.acquire(priority="interactive",deadline=time.monotonic()+5)
    call = Mock()
    try:
        with pytest.raises(LocalModelBusy):
            with request_scope():
                run_http(call,purpose="embedding")
                run_http(call,purpose="rerank")
        call.assert_not_called()
    finally:
        owner.close()


def test_early_scope_exit_holds_owner_until_inflight_http_finishes(coordinator):
    entered, release = threading.Event(),threading.Event()
    from contextvars import copy_context
    with ThreadPoolExecutor(max_workers=1) as pool:
        with request_scope() as permit:
            context = copy_context()
            future = pool.submit(context.run,run_http,
                lambda _: (entered.set(),release.wait(2),"ignored")[-1],purpose="embedding")
            assert entered.wait(1)
        assert coordinator.owner == permit.token
        with pytest.raises(LocalModelDeadline):
            permit.http(lambda _: None,purpose="rerank")
        release.set()
        with pytest.raises(LocalModelDeadline):
            future.result()
    assert coordinator.owner is None


def test_ownership_loss_rejects_before_dispatch_and_cannot_release_successor(coordinator):
    permit = coordinator.acquire(priority="interactive",deadline=time.monotonic()+5)
    coordinator.release(permit.token)
    successor = coordinator.acquire(priority="interactive",deadline=time.monotonic()+5)
    call = Mock()
    try:
        with pytest.raises(LocalModelDeadline):
            permit.http(call,purpose="embedding")
        permit.close()
        assert coordinator.valid(successor.token)
        call.assert_not_called()
    finally:
        successor.close()


def test_ownership_loss_during_http_rejects_return_and_does_not_delete_new_owner(coordinator):
    old = coordinator.acquire(priority="interactive",deadline=time.monotonic()+5)
    successor = None
    def changed(_):
        nonlocal successor
        coordinator.release(old.token)
        successor = coordinator.acquire(priority="interactive",deadline=time.monotonic()+5)
        return "must-not-publish"
    try:
        with pytest.raises(LocalModelDeadline):
            old.http(changed,purpose="embedding")
        old.close()
        assert coordinator.valid(successor.token)
    finally:
        if successor:
            successor.close()


def test_parallel_local_bge_batches_inherit_one_turn_permit(coordinator,monkeypatch):
    from app.services.reranker_service import RerankerService
    service = RerankerService(local_base="http://127.0.0.1:7999",local_model="BAAI/bge-reranker-large")
    acquire = Mock(wraps=coordinator.acquire)
    monkeypatch.setattr(coordinator,"acquire",acquire)
    def scored(query,docs,top_n):
        return run_http(lambda _: [(i,float(text)) for i,text in enumerate(docs)],purpose="rerank")
    monkeypatch.setattr(service._providers[0],"rerank",scored)
    with request_scope():
        ranked=service.rerank_sync("合成",[{"id":str(i),"content":str(i)} for i in range(12)],3)
    assert [r["id"] for r in ranked]==["11","10","9"]
    acquire.assert_called_once()


def test_redis_coordination_fault_is_busy_not_process_fallback():
    client = Mock()
    client.eval.side_effect = ConnectionError("synthetic-private-error")
    coordinator = RedisCoordinator(client)
    with pytest.raises(LocalModelBusy,match="coordination_unavailable"):
        coordinator.acquire(priority="interactive",deadline=time.monotonic()+1)


def test_late_redis_acquisition_is_released_without_model_dispatch():
    from app.services.local_admission import RELEASE
    client = Mock()
    def delayed(script,*args):
        if script != RELEASE:
            time.sleep(.06)
        return 1
    client.eval.side_effect = delayed
    coord = RedisCoordinator(client)
    with pytest.raises(LocalModelBusy,match="late_local_request_admission"):
        coord.acquire(priority="background",deadline=time.monotonic()+1)
    assert any(c.args[0] == RELEASE for c in client.eval.call_args_list)


def test_redis_mode_requires_redis_and_cannot_use_process_when_disabled(monkeypatch):
    from app.config.config import settings
    from app.services.local_admission import get_coordinator
    monkeypatch.setattr(settings,"LOCAL_MODEL_COORDINATOR","redis")
    monkeypatch.setattr(settings,"REDIS_ENABLED",False)
    with pytest.raises(LocalModelBusy,match="redis_required"):
        get_coordinator()


def test_cached_retrieval_needs_no_second_whole_admission(kb,monkeypatch):
    from types import SimpleNamespace
    from tests.test_m3_lifecycle import execute
    from app.config.config import settings
    monkeypatch.setattr(settings,"RAG_USE_RERANKER",False)
    monkeypatch.setattr(kb._embedding_service,"_embedding",
                        SimpleNamespace(api_base="http://127.0.0.1:8013/v1",model="synthetic"),raising=False)
    execute(kb,kb.add_document("合成","合成商品规则"))
    first = kb.search("合成")
    assert first["results"]
    # 本测试仅确认已命中的缓存先于准入执行；SyntheticEmbedding避免模型网络。
    reject = Mock(side_effect=LocalModelBusy("must-not-acquire"))
    monkeypatch.setattr("app.services.local_admission.get_coordinator",reject)
    assert kb.search("合成")["from_cache"]
    reject.assert_not_called()


def test_retrieval_busy_before_embedding_or_rerank_executes(kb,coordinator,monkeypatch):
    from types import SimpleNamespace
    from tests.test_m3_lifecycle import execute
    from app.config.config import settings
    from app.services.reranker_service import RerankerService
    monkeypatch.setattr(kb._embedding_service,"_embedding",
                        SimpleNamespace(api_base="http://127.0.0.1:8013/v1",model="synthetic"),raising=False)
    execute(kb,kb.add_document("合成","合成商品检索条件"))
    monkeypatch.setattr(settings,"RAG_USE_RERANKER",True)
    kb._reranker=RerankerService(local_base="http://127.0.0.1:7999",local_model="synthetic")
    encoder=Mock();rerank=Mock()
    monkeypatch.setattr(kb._embedding_service,"encode_query",encoder)
    monkeypatch.setattr(kb._reranker,"rerank_with_deadline",rerank)
    coordinator.max_waiters=0
    owner=coordinator.acquire(priority="interactive",deadline=time.monotonic()+5)
    try:
        result=kb.search("合成")
        assert not result["results"] and result["degraded_reason"]=="local_model_busy"
        assert result["meta"]["local_request_admission"][0]["status"]=="busy"
        assert result["evidence_bundle"]["items"]==[]
        encoder.assert_not_called();rerank.assert_not_called()
    finally:
        owner.close()


from tests.test_m3_lifecycle import kb as kb
