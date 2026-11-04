"""共享HTTP池与绝对阶段预算；合成响应，不访问模型/外部供应商。"""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest

from app.services import reranker_service as module


def provider():
    return module._RerankProvider("local", "http://127.0.0.1:7999", "", "synthetic", "openai", 12)


def response():
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"results": []})


def test_reuses_client_and_reset_releases_it_once(monkeypatch):
    client = Mock()
    client.post.return_value = response()
    factory = Mock(return_value=client)
    monkeypatch.setattr(module.httpx, "Client", factory)
    svc = module.RerankerService(local_base="http://127.0.0.1:7999")
    item = svc._providers[0]
    item._post_uncoordinated("http://127.0.0.1:7999/rerank", {})
    item._post_uncoordinated("http://127.0.0.1:7999/rerank", {})
    factory.assert_called_once()
    assert factory.call_args.kwargs["trust_env"] is False
    assert client.post.call_count == 2
    client.close.assert_not_called()
    monkeypatch.setattr(module, "_reranker", svc)
    module.reset_reranker()
    module.reset_reranker()
    assert module._reranker is None
    client.close.assert_called_once()


@pytest.mark.parametrize("construct_seconds,dispatch", [(2, True), (5.1, False)])
def test_construction_consumes_original_budget(monkeypatch, construct_seconds, dispatch):
    clock = [100.]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    client = Mock()
    client.post.return_value = response()
    def construct(**kwargs):
        clock[0] += construct_seconds
        return client
    monkeypatch.setattr(module.httpx, "Client", construct)
    item = provider()
    try:
        if dispatch:
            item._post_uncoordinated("synthetic", {}, remaining=5)
            assert client.post.call_args.kwargs["timeout"].read == 3
        else:
            with pytest.raises(TimeoutError, match="before_http"):
                item._post_uncoordinated("synthetic", {}, remaining=5)
            client.post.assert_not_called()
    finally:
        item.close()
    client.close.assert_called_once()


def test_late_body_is_rejected_even_if_transport_returns_success(monkeypatch):
    clock = [100.]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    client = Mock()
    def post(*args, **kwargs):
        clock[0] += 5.1
        return response()
    client.post.side_effect = post
    monkeypatch.setattr(module.httpx, "Client", Mock(return_value=client))
    item = provider()
    try:
        with pytest.raises(TimeoutError, match="after_http"):
            item._post_uncoordinated("synthetic", {}, remaining=5)
    finally:
        item.close()


def test_concurrent_first_calls_create_only_one_client(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    client = Mock()
    client.post.return_value = response()
    def construct(**kwargs):
        entered.set()
        assert release.wait(2)
        return client
    factory = Mock(side_effect=construct)
    monkeypatch.setattr(module.httpx, "Client", factory)
    item = provider()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(item._post_uncoordinated, "synthetic", {}, 5)
            assert entered.wait(1)
            two = pool.submit(item._post_uncoordinated, "synthetic", {}, 5)
            release.set()
            assert one.result() == two.result() == {"results": []}
        factory.assert_called_once()
        assert client.post.call_count == 2
    finally:
        release.set()
        item.close()


def test_close_does_not_close_inflight_client_and_rejects_new_work(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    client = Mock()
    def post(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return response()
    client.post.side_effect = post
    monkeypatch.setattr(module.httpx, "Client", Mock(return_value=client))
    item = provider()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(item._post_uncoordinated, "synthetic", {}, 5)
        try:
            assert entered.wait(1)
            item.close()
            client.close.assert_not_called()
            with pytest.raises(RuntimeError, match="reranker_closed"):
                item._post_uncoordinated("synthetic", {}, remaining=5)
        finally:
            release.set()
        assert pending.result() == {"results": []}
    item.close()
    client.close.assert_called_once()


def test_lock_contention_consumes_deadline_without_http_dispatch(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(module.httpx, "Client", factory)
    item = provider()
    with item._client_lock:
        with pytest.raises(TimeoutError, match="waiting_for_client"):
            item._post_uncoordinated("synthetic", {}, remaining=.01)
    factory.assert_not_called()
    item.close()


def test_concurrent_singleton_initialization_does_not_orphan_client_owners(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    service = Mock()
    def create(**kwargs):
        entered.set()
        assert release.wait(2)
        return service
    factory = Mock(side_effect=create)
    monkeypatch.setattr(module, "_reranker", None)
    monkeypatch.setattr(module, "RerankerService", factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(module.get_reranker)
        try:
            assert entered.wait(1)
            two = pool.submit(module.get_reranker)
        finally:
            release.set()
        assert one.result() is two.result() is service
    factory.assert_called_once()
    module.reset_reranker()
    service.close.assert_called_once()


def test_close_attempts_both_providers_even_when_first_close_fails():
    svc = module.RerankerService()
    first, second = Mock(), Mock()
    first.close.side_effect = RuntimeError("synthetic-close-failure")
    svc._providers = [first, second]
    with pytest.raises(RuntimeError, match="synthetic-close-failure"):
        svc.close()
    second.close.assert_called_once()
