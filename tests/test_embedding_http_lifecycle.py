"""Embedding常驻HTTP池的并发初始化、关闭与原预算边界。"""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import threading
import time

import pytest

from app.services import embedding_service as module
from app.services.local_inference import LocalModelDeadline


def embedding(base="http://127.0.0.1:8013/v1"):
    return module.APIEmbedding(base,"synthetic-only","synthetic",dim=3,max_retries=0,timeout=5)


def response():
    return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{"data":[{"index":0,"embedding":[1,2,3]}]})


@pytest.mark.parametrize("base,trust",[("http://127.0.0.1:8013/v1",False),("https://synthetic.invalid/v1",True)])
def test_reuses_client_and_preserves_external_proxy_behavior(monkeypatch,base,trust):
    client=Mock(is_closed=False)
    client.post.return_value=response()
    factory=Mock(return_value=client)
    monkeypatch.setattr(module.httpx,"Client",factory)
    item=embedding(base)
    try:
        assert item.encode("one")==item.encode("two")==[1.,2.,3.]
        factory.assert_called_once()
        assert factory.call_args.kwargs["trust_env"] is trust
        assert client.post.call_count==2
    finally:item.close()
    item.close()
    client.close.assert_called_once()


def test_concurrent_first_initialization_builds_one_managed_client(monkeypatch):
    entered,release=threading.Event(),threading.Event()
    client=Mock(is_closed=False)
    def construct(**kwargs):
        entered.set()
        assert release.wait(2)
        return client
    factory=Mock(side_effect=construct)
    monkeypatch.setattr(module.httpx,"Client",factory)
    item=embedding()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(item._get_client)
        assert entered.wait(1)
        second=pool.submit(item._get_client)
        release.set()
        assert first.result() is second.result() is client
    item.close()
    factory.assert_called_once()
    client.close.assert_called_once()


def test_close_waits_for_inflight_http_and_refuses_new_client(monkeypatch):
    entered,release=threading.Event(),threading.Event()
    client=Mock(is_closed=False)
    def post(*args,**kwargs):
        entered.set()
        assert release.wait(2)
        return response()
    client.post.side_effect=post
    monkeypatch.setattr(module.httpx,"Client",Mock(return_value=client))
    item=embedding()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(item.encode,"one")
        assert entered.wait(1)
        item.close()
        client.close.assert_not_called()
        with pytest.raises(RuntimeError,match="embedding_closed"):
            item._get_client()
        release.set()
        assert pending.result()==[1.,2.,3.]
    item.close()
    client.close.assert_called_once()


def test_construction_expiry_never_dispatches_or_populates_cache(monkeypatch):
    clock=[time.monotonic()]
    monkeypatch.setattr(module,"time",SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda _:None))
    client=Mock(is_closed=False)
    def construct(**kwargs):
        clock[0]+=6
        return client
    monkeypatch.setattr(module.httpx,"Client",construct)
    item=embedding()
    try:
        with pytest.raises(LocalModelDeadline):item.encode("one")
        client.post.assert_not_called()
        assert item._text_cache=={}
    finally:item.close()


def test_borrow_lock_wait_is_deadline_bounded(monkeypatch):
    item=embedding()
    entered,release=threading.Event(),threading.Event()
    def hold():
        with item._client_lock:
            entered.set()
            release.wait(2)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(hold)
        assert entered.wait(1)
        try:
            with pytest.raises(TimeoutError):
                with item._borrow_client(time.monotonic()+.01):pass
        finally:release.set()
        pending.result()
    item.close()


def test_late_json_is_not_cached(monkeypatch):
    clock=[time.monotonic()]
    monkeypatch.setattr(module,"time",SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda _:None))
    client=Mock(is_closed=False)
    def decode():
        clock[0]+=6
        return {"data":[{"index":0,"embedding":[1,2,3]}]}
    client.post.return_value=SimpleNamespace(raise_for_status=lambda:None,json=decode)
    monkeypatch.setattr(module.httpx,"Client",Mock(return_value=client))
    item=embedding()
    try:
        with pytest.raises(LocalModelDeadline):item.encode("one")
        assert item._text_cache=={}
    finally:item.close()


def test_partial_client_initialization_cost_is_subtracted_from_http_timeout(monkeypatch):
    clock=[time.monotonic()]
    monkeypatch.setattr(module,"time",SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda _:None))
    client=Mock(is_closed=False)
    client.post.return_value=response()
    def construct(**kwargs):
        clock[0]+=2
        return client
    monkeypatch.setattr(module.httpx,"Client",construct)
    item=embedding()
    try:
        assert item.encode("one")==[1.,2.,3.]
        assert 0 < client.post.call_args.kwargs["timeout"] <= 3
    finally:item.close()


def test_admission_rejection_does_not_construct_client_or_cache(monkeypatch):
    from app.services.local_inference import LocalModelBusy
    factory=Mock()
    monkeypatch.setattr(module.httpx,"Client",factory)
    monkeypatch.setattr("app.services.local_admission.get_coordinator",Mock(side_effect=LocalModelBusy("synthetic-busy")))
    item=embedding()
    try:
        with pytest.raises(LocalModelBusy):item.encode("one")
        factory.assert_not_called()
        assert item._text_cache=={}
    finally:item.close()


def test_query_and_document_http_timeouts_are_independent(monkeypatch):
    client = Mock(is_closed=False)
    client.post.return_value = response()
    monkeypatch.setattr(module.httpx, "Client", Mock(return_value=client))
    item = module.APIEmbedding("https://synthetic.invalid/v1", "synthetic-only",
                               "synthetic", dim=3, max_retries=0, timeout=10,
                               document_timeout=60)
    try:
        item.encode_query("query")
        item.encode_batch(["document"])
        assert [call.kwargs["timeout"] for call in client.post.call_args_list] == [10, 60]
        assert item.timeout == 10
    finally:
        item.close()
