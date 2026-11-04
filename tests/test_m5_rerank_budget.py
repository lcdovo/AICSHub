"""完整候选、统一截止时间、共享资源及失效回包；只用合成分数。"""
import threading
import time
from unittest.mock import Mock

import pytest

from app.services.reranker_service import RerankerService, _RerankProvider, _RERANK_DEADLINE


def service():
    return RerankerService(local_base="http://local.test", local_model="BAAI/bge-reranker-large")


def candidates(count=20):
    return [{"id":f"c{i}","content":str(i),"condition":"完整例外仍保留"} for i in range(count)]


def test_local_batching_scores_all_twenty_candidates_and_can_promote_tail(monkeypatch):
    svc=service(); calls=[]
    def score(query,docs,top_n):
        calls.append(list(docs));assert top_n==len(docs)
        return [(i,float(text)) for i,text in enumerate(docs)]
    monkeypatch.setattr(svc._providers[0],"rerank",score)
    pool=candidates()
    ranked=svc.rerank_sync("合成",pool,3,deadline=time.monotonic()+2)
    assert [r["id"] for r in ranked]==["c19","c18","c17"]
    assert len(calls)==5 and all(len(c)<=4 for c in calls)
    assert sorted(int(t) for batch in calls for t in batch)==list(range(20))
    assert ranked[0]["condition"]=="完整例外仍保留" and svc.last_used_provider=="local"
    assert all("rerank_score" not in c for c in pool)


def test_over_capacity_cannot_silently_drop_twenty_fifth_candidate(monkeypatch):
    svc=service(); call=Mock(side_effect=AssertionError("unsupported pool must not dispatch"))
    monkeypatch.setattr(svc._providers[0],"rerank",call)
    assert svc.rerank_sync("合成",candidates(25),5) is None
    call.assert_not_called()


@pytest.mark.parametrize("invalid", ["missing","duplicate","negative","bool_index","bool_score","nan","inf"])
def test_any_bad_batch_invalidates_entire_ranking(monkeypatch,invalid):
    svc=service()
    def score(query,docs,top_n):
        values=[(i,float(t)) for i,t in enumerate(docs)]
        if "4" in docs:
            if invalid=="missing":values.pop()
            elif invalid=="duplicate":values[-1]=values[0]
            elif invalid=="negative":values[0]=(-1,.5)
            elif invalid=="bool_index":values[0]=(True,.5)
            elif invalid=="bool_score":values[0]=(0,True)
            else:values[0]=(0,float(invalid))
        return values
    monkeypatch.setattr(svc._providers[0],"rerank",score)
    assert svc.rerank_sync("合成",candidates(8),3) is None
    assert svc.last_used_provider is None


def test_timeout_stops_undispatched_batches_and_blocks_late_publish(monkeypatch):
    svc=service(); release=threading.Event(); calls=[]; finished=[]; done=threading.Event()
    def score(query,docs,top_n):
        calls.append(list(docs))
        try:
            release.wait(2)
            return [(i,float(t)) for i,t in enumerate(docs)]
        finally:
            finished.append(True)
            if len(finished)==2:done.set()
    monkeypatch.setattr(svc._providers[0],"rerank",score)
    try:
        assert svc.rerank_sync("合成",candidates(20),3,deadline=time.monotonic()+.05) is None
        assert len(calls)==2 and svc.last_used_provider is None
    finally:
        release.set()
        # 等已经派发的替身归还全局槽；不把迟到回包重新纳入排序。
        assert done.wait(2)
    assert svc.last_used_provider is None


def test_expired_deadline_does_not_start_any_provider(monkeypatch):
    svc=service(); call=Mock()
    monkeypatch.setattr(svc._providers[0],"rerank",call)
    assert svc.rerank_sync("合成",candidates(),3,deadline=time.monotonic()-1) is None
    call.assert_not_called()


def test_expired_external_attempt_cannot_later_dispatch_local_provider(monkeypatch):
    svc=RerankerService(api_base="http://external.test",model="m",local_base="http://local.test")
    external,local=svc._providers
    def slow(*a,**kw):
        time.sleep(.03)
        raise TimeoutError("synthetic")
    monkeypatch.setattr(external,"rerank",slow)
    call=Mock();monkeypatch.setattr(local,"rerank",call)
    assert svc.rerank_sync("合成",candidates(2),2,deadline=time.monotonic()+.01) is None
    call.assert_not_called()


def test_http_read_timeout_is_capped_by_remaining_shared_deadline(monkeypatch):
    captured={}
    class Client:
        def __init__(self,**kw):captured.update(kw)
        def post(self,*a,**kw):
            captured["request_timeout"]=kw["timeout"]
            raise TimeoutError("synthetic")
        def close(self):captured["closed"]=True
    monkeypatch.setattr("app.services.reranker_service.httpx.Client",Client)
    provider=_RerankProvider("local","http://local.test","","BAAI/bge-reranker-large","openai",12)
    token=_RERANK_DEADLINE.set(time.monotonic()+.4)
    try:
        with pytest.raises(TimeoutError):provider._post("http://local.test/rerank",{})
    finally:
        _RERANK_DEADLINE.reset(token)
        provider.close()
    assert 0<captured["request_timeout"].read<=.4 and captured["closed"]


def test_external_provider_is_not_split_into_extra_paid_calls(monkeypatch):
    svc=RerankerService(api_base="http://external.test",model="synthetic")
    call=Mock(return_value=[(i,float(i)) for i in range(20)])
    monkeypatch.setattr(svc._providers[0],"rerank",call)
    assert len(svc.rerank_sync("合成",candidates(),5))==5
    call.assert_called_once()


def test_parallel_batch_limit_is_shared_by_multiple_service_instances(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    one,two=service(),service()
    lock=threading.Lock(); release=threading.Event(); two_entered=threading.Event()
    active=0; peak=0
    def score(query,docs,top_n):
        nonlocal active,peak
        with lock:
            active+=1;peak=max(peak,active)
            if active==2:two_entered.set()
        try:
            assert release.wait(2)
            return [(i,float(t)) for i,t in enumerate(docs)]
        finally:
            with lock:active-=1
    for svc in [one,two]:monkeypatch.setattr(svc._providers[0],"rerank",score)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=[pool.submit(svc.rerank_sync,"合成",candidates(12),3,deadline=time.monotonic()+3) for svc in [one,two]]
        try:
            assert two_entered.wait(1)
            with lock:assert active==peak==2
        finally:release.set()
        assert all([r["id"] for r in f.result()]==["c11","c10","c9"] for f in pending)
    assert peak==2


def test_other_local_model_keeps_single_request_semantics(monkeypatch):
    svc=RerankerService(local_base="http://local.test",local_model="other-model")
    call=Mock(return_value=[(i,float(i)) for i in reversed(range(12))])
    monkeypatch.setattr(svc._providers[0],"rerank",call)
    assert len(svc.rerank_sync("合成",candidates(12),3))==3
    call.assert_called_once()


def test_different_phase_budget_does_not_reuse_previous_ranking_cache(monkeypatch, tmp_path):
    from tests.test_m3_lifecycle import SyntheticEmbedding, execute
    from app.models.models import Base
    from app.repositories.knowledge_repository import KnowledgeRepository
    from app.services.knowledge_base import KnowledgeBaseService, versioned_search
    from app.config.config import settings
    monkeypatch.setattr(settings,"USE_MILVUS",False)
    repo=KnowledgeRepository("sqlite:///"+(tmp_path/"budget.db").as_posix())
    Base.metadata.create_all(repo.engine)
    kb=KnowledgeBaseService(repository=repo,embedding=SyntheticEmbedding())
    try:
        execute(kb,kb.add_document("合成","合成商品定制例外"))
        first=kb.search("合成")
        assert not first["from_cache"] and kb.search("合成")["from_cache"]
        monkeypatch.setattr(versioned_search,"ENCODE_TIMEOUT_SECONDS",4.0)
        changed=kb.search("合成")
        assert not changed["from_cache"] and changed["meta"]["rerank_budget_ms"]==4000
    finally:repo.engine.dispose()
