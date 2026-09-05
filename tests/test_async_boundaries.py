import asyncio

import pytest

from app.services.embedding_service import EmbeddingService
from app.services.knowledge_base import KnowledgeBaseService


@pytest.mark.asyncio
async def test_embedding_async_facade_runs_and_returns_vector(monkeypatch):
    service = EmbeddingService()
    called = False

    async def fake_to_thread(func, *args, **kwargs):
        nonlocal called
        called = True
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    vector = await service.async_encode("退款政策")
    assert called is True
    assert len(vector) == service.dim


@pytest.mark.asyncio
async def test_knowledge_base_async_search_delegates_to_thread(monkeypatch):
    service = KnowledgeBaseService()
    called = False

    async def fake_to_thread(func, *args, **kwargs):
        nonlocal called
        called = True
        return {"success": True, "results": []}

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    result = await service.async_search("不存在的问题", top_k=1)
    assert called is True
    assert result["success"] is True
