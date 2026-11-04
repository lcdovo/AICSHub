"""实际FastAPI lifespan，外部资源清理函数替身；不启动业务服务。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("body_error", [False, True])
async def test_lifespan_closes_actual_chat_graph_and_other_resources_despite_failure(monkeypatch, caplog, failed, body_error):
    import app.main as main
    from app.services.chat_service import chat_service

    health = SimpleNamespace(close=AsyncMock())
    chat = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("synthetic-private-error") if failed else None))
    redis = AsyncMock()
    mcp = AsyncMock()
    reranker = Mock()
    monkeypatch.setattr(main, "llm_service", health)
    monkeypatch.setattr(chat_service, "agent_graph", SimpleNamespace(llm_service=chat))
    monkeypatch.setattr(main, "init_database", AsyncMock())
    monkeypatch.setattr(main, "close_redis", redis)
    monkeypatch.setattr(main, "shutdown_langfuse", Mock())
    monkeypatch.setattr("app.agent.mcp_client.shutdown_mcp_client", mcp)
    monkeypatch.setattr("app.services.reranker_service.reset_reranker", reranker)
    monkeypatch.setattr("app.utils.otel.enable_otlp_export", lambda: None)

    async def lifecycle():
        async with main.lifespan(main.app):
            if body_error:
                raise ValueError("synthetic-body-error")

    if body_error:
        with pytest.raises(ValueError, match="synthetic-body-error"):
            await lifecycle()
    else:
        await lifecycle()
    health.close.assert_awaited_once()
    chat.close.assert_awaited_once()
    mcp.assert_awaited_once()
    redis.assert_awaited_once()
    reranker.assert_called_once()
    assert "synthetic-private-error" not in caplog.text
