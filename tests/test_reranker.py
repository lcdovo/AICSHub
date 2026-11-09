"""Reranker 服务与缓存一致性测试。

覆盖：
1. openai / dashscope / TEI 裸列表三种响应格式解析
2. 单 provider 失败返回 None；provider 链：外部失败→本地成功
3. 无 provider 时不可用；rrrank_sync 短路
4. 生产版本化检索的重排路由另见test_m3_search.py
5. _flush_shared_caches_sync：清本地 Dict 并触发 Redis 前缀清理
"""
import asyncio

import pytest

from app.services.knowledge_base import KnowledgeBaseService
from app.services.reranker_service import (
    PROVIDER_EXTERNAL,
    PROVIDER_LOCAL,
    RerankerService,
    _RerankProvider,
)


# ---------- 响应格式解析 ----------


def _provider(style="openai", name=PROVIDER_EXTERNAL):
    return _RerankProvider(
        name=name,
        base="http://rerank.test",
        api_key="",
        model="m",
        style=style,
        timeout=3.0,
    )


def test_openai_style_parse(monkeypatch):
    p = _provider(style="openai")
    monkeypatch.setattr(
        p, "_post",
        lambda url, payload: {"results": [
            {"index": 1, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.7},
        ]},
    )
    assert p.rerank("q", ["a", "b"], top_n=2) == [(1, 0.9), (0, 0.7)]


def test_dashscope_style_parse(monkeypatch):
    p = _provider(style="dashscope")
    captured = {}

    def _post(url, payload):
        captured["url"] = url
        captured["payload"] = payload
        return {"output": {"results": [{"index": 0, "relevance_score": 0.6}]}}

    monkeypatch.setattr(p, "_post", _post)
    assert p.rerank("q", ["a"], top_n=1) == [(0, 0.6)]
    assert captured["url"].endswith("/services/rerank/text-rerank/generation")
    assert captured["payload"]["input"]["query"] == "q"
    assert captured["payload"]["input"]["documents"] == ["a"]


def test_tei_bare_list_parse(monkeypatch):
    p = _provider(style="openai")
    monkeypatch.setattr(p, "_post", lambda url, payload: [{"index": 1, "score": 0.8}])
    assert p.rerank("q", ["a", "b"], top_n=2) == [(1, 0.8)]


def test_parse_skips_malformed_items(monkeypatch):
    p = _provider(style="openai")
    monkeypatch.setattr(
        p, "_post",
        lambda url, payload: {"results": [
            {"index": "x", "relevance_score": 0.9},  # 非法 index
            {"index": 2, "score": 0.5},
        ]},
    )
    assert p.rerank("q", ["a", "b", "c"], top_n=3) == [(2, 0.5)]


# ---------- RerankerService provider 链 ----------


def _svc_with(monkeypatch, external_raw=None, external_raise=None, local_raw=None):
    svc = RerankerService(
        api_base="http://external.test", api_key="k", model="m", style="openai",
        local_base="http://local.test:7997",
    )
    ext, loc = svc._providers
    if external_raw is not None:
        monkeypatch.setattr(ext, "_post", lambda url, payload: external_raw)
    if external_raise is not None:
        def boom(url, payload):
            raise external_raise
        monkeypatch.setattr(ext, "_post", boom)
    if local_raw is not None:
        monkeypatch.setattr(loc, "_post", lambda url, payload: local_raw)
    return svc


def test_external_fails_local_succeeds(monkeypatch):
    svc = _svc_with(
        monkeypatch,
        external_raise=TimeoutError("timeout"),
        local_raw={"results": [{"index": 1, "relevance_score": 0.9},
                               {"index": 0, "relevance_score": 0.2}]},
    )
    candidates = [{"id": "a", "content": "x"}, {"id": "b", "content": "y"}]
    out = svc.rerank_sync("q", candidates, top_k=2)
    assert out is not None
    assert out[0]["id"] == "b"
    assert out[0]["final_score"] == pytest.approx(0.9)
    assert svc.last_used_provider == PROVIDER_LOCAL


def test_all_providers_fail_returns_none(monkeypatch):
    svc = _svc_with(monkeypatch, external_raise=TimeoutError("t"))
    # 本地也失败
    for p in svc._providers:
        def boom(url, payload):
            raise TimeoutError("t")
        monkeypatch.setattr(p, "_post", boom)
    assert svc.rerank_sync("q", [{"id": "a", "content": "x"}], top_k=1) is None


def test_no_providers_unavailable():
    svc = RerankerService()
    assert not svc.is_available()
    assert svc.rerank_sync("q", [{"id": "a", "content": "x"}], top_k=1) is None


def test_top_k_truncation_and_scores(monkeypatch):
    svc = RerankerService(api_base="http://x", api_key="", model="m")
    p = svc._providers[0]
    monkeypatch.setattr(
        p, "_post",
        lambda url, payload: {"results": [
            {"index": 0, "relevance_score": 0.3},
            {"index": 1, "relevance_score": 0.9},
            {"index": 2, "relevance_score": 0.6},
        ]},
    )
    docs = [{"id": f"d{i}", "content": f"c{i}"} for i in range(3)]
    out = svc.rerank_sync("q", docs, top_k=2)
    assert [d["id"] for d in out] == ["d1", "d2"]
    assert out[0]["rerank_score"] == pytest.approx(0.9)
    assert svc.last_used_provider == PROVIDER_EXTERNAL


# ---------- 缓存一致性 ----------


def test_flush_shared_caches_clears_local_and_deletes_prefixes(monkeypatch):
    svc = KnowledgeBaseService()
    cleared = []
    monkeypatch.setattr(svc._query_cache, "clear", lambda: cleared.append("local"))
    flushed_kb = []
    flushed_redis = []

    async def fake_kb_flush():
        flushed_kb.append("kb")

    async def fake_redis_flush():
        flushed_redis.append("redis")

    monkeypatch.setattr(svc._query_cache, "_flush_redis_kb_cache", fake_kb_flush)
    monkeypatch.setattr(
        "app.utils.redis_cache.flush_all_redis_object_caches", fake_redis_flush
    )

    svc._flush_shared_caches_sync()
    assert cleared == ["local"]
    assert flushed_kb == ["kb"]
    assert flushed_redis == ["redis"]


def test_flush_shared_caches_in_running_loop_only_local(monkeypatch):
    async def scenario():
        svc = KnowledgeBaseService()
        cleared = []
        monkeypatch.setattr(svc._query_cache, "clear", lambda: cleared.append("local"))
        called = []
        monkeypatch.setattr(svc._query_cache, "_flush_redis_kb_cache", lambda: called.append("x"))
        svc._flush_shared_caches_sync()  # 运行中 loop 内：仅清本地，跳过 Redis
        return cleared, called

    cleared, called = asyncio.run(scenario())
    assert cleared == ["local"]
    assert called == []  # 未触发 Redis 清理，避免 asyncio.run 与运行中 loop 冲突
