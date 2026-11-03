"""AC-08-02通知分支：没有投递适配器不得构造sent回执。"""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select

from app.agent.tools import SendNotificationTool, ToolExecutionResult
from app.models.models import NotificationRecord, NotificationStatus, OperationAudit
from tests.test_ticket_idempotency import api_context


@pytest.mark.parametrize("channel", ["sms", "email", "inapp"])
async def test_missing_adapter_never_returns_delivery_receipt(monkeypatch, channel):
    generated_id = Mock(return_value="synthetic-id")
    database = Mock(side_effect=AssertionError("disabled delivery must not access database"))
    monkeypatch.setattr("app.agent.tools.uuid.uuid4", generated_id)
    monkeypatch.setattr("app.utils.database.async_session", database)
    result = await SendNotificationTool().execute(channel=channel, content="合成通知内容", user_id=101)
    assert result["success"] is False
    assert not result.get("data")
    assert result["error_type"] == "channel_unavailable"
    assert result["retryable"] is False
    generated_id.assert_not_called()
    database.assert_not_called()


@pytest.mark.parametrize("channel", ["sms", "email", "inapp"])
async def test_wrapper_ignores_old_fake_success_cache_and_does_not_retry(channel, monkeypatch):
    tool = SendNotificationTool()
    tool._idempotency_cache["old-k"] = ToolExecutionResult(success=True, data={"status": "sent", "notification_id": "old-fake"})
    calls = 0
    original = tool.execute

    async def count(**kwargs):
        nonlocal calls
        calls += 1
        return await original(**kwargs)

    monkeypatch.setattr(tool, "execute", count)
    result = await tool.execute_with_retry(channel=channel, content="合成通知内容", user_id=101, idempotency_key="old-k")
    assert not result.success and result.data is None
    assert result.error_type == "channel_unavailable" and result.retry_count == 0
    assert calls == 1
    assert set(tool._idempotency_cache) == {"old-k"}  # 不新增缓存，也不误取旧结果


@pytest.mark.parametrize("field,value", [("user_id", True), ("user_id", "101"), ("user_id", -1),
                                        ("content", None), ("content", 123), ("content", "   "),
                                        ("channel", "unknown"), ("force_send", True)])
async def test_invalid_arguments_are_explicit_failures(field, value):
    arguments = {"channel": "email", "content": "合成通知内容", "user_id": 101, field: value}
    result = await SendNotificationTool().execute(**arguments)
    assert result["success"] is False and not result.get("data")
    assert result["error_type"] == "validation"


async def test_blocked_send_preserves_pending_business_notification(tmp_path, monkeypatch):
    async with api_context(tmp_path / "notification.db") as (client, factory):
        response = await client.post("/api/v1/tickets", json={"user_id": 101, "category": "技术支持", "content": "合成问题"})
        assert response.status_code == 200

        async def snapshot():
            async with factory() as db:
                item = await db.scalar(select(NotificationRecord))
                return (item.id, item.status, item.attempts, item.sent_at, item.payload,
                        await db.scalar(select(func.count()).select_from(NotificationRecord)),
                        await db.scalar(select(func.count()).select_from(OperationAudit)))

        before = await snapshot()
        assert before[1] == NotificationStatus.PENDING and before[3] is None
        monkeypatch.setattr("app.utils.database.async_session", factory)
        result = await SendNotificationTool().execute(channel="inapp", content="合成通知内容", user_id=101)
        assert not result["success"]
        assert await snapshot() == before


@pytest.mark.parametrize("channel", ["sms", "email", "inapp"])
async def test_inprocess_mcp_also_reports_no_delivery(channel, monkeypatch):
    from mcp.shared.memory import create_connected_server_and_client_session
    from app.agent.mcp_server import mcp
    from app.agent.mcp_client import _extract_text_content

    database = Mock(side_effect=AssertionError("disabled delivery must not access database"))
    monkeypatch.setattr("app.utils.database.async_session", database)
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        await client.initialize()
        listed = await client.list_tools()
        advertised = next(tool for tool in listed.tools if tool.name == "send_notification")
        assert "未接入" in advertised.description
        raw = await client.call_tool("send_notification", {"channel": channel, "content": "合成通知内容", "user_id": 101})
        result = json.loads(_extract_text_content(raw))
        assert not result["success"] and result["data"] is None
        assert result["error_type"] == "channel_unavailable"
    database.assert_not_called()


@pytest.mark.parametrize("mcp_enabled", [False, True])
async def test_registry_direct_and_mcp_fallback_do_not_fake_success(monkeypatch, mcp_enabled):
    import app.agent.mcp_client as client

    async def unavailable(*args, **kwargs):
        return None

    monkeypatch.setattr(client, "_ENABLED", mcp_enabled)
    monkeypatch.setattr(client, "_get_client", lambda: SimpleNamespace(call=unavailable))
    for channel in ("sms", "email", "inapp"):
        result = await client.invoke_tool("send_notification", channel=channel, content="合成通知内容", user_id=101)
        assert not result.success and result.data is None
        assert result.error_type == "channel_unavailable" and result.retry_count == 0
