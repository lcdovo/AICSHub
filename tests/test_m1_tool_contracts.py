from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent import mcp_client
from app.agent.tools import ToolExecutionResult
from tests.test_ticket_idempotency import api_context
from tests.test_ticket_tool import arguments, capability, seed_message


async def test_unknown_tool_explicit_failure():
    result = await mcp_client.invoke_tool("not-installed")
    assert not result.success and result.error_type == "not_found"


async def test_missing_write_receipt_is_not_success(monkeypatch):
    monkeypatch.setattr(mcp_client, "_ENABLED", True)
    monkeypatch.setattr(mcp_client, "_get_client", lambda: SimpleNamespace(call=AsyncMock(
        return_value=ToolExecutionResult(success=True, data={"message": "done"})) ))
    result = await mcp_client.invoke_tool("create_ticket")
    assert not result.success and result.error_type == "unknown"


async def test_unknown_outcome_does_not_execute_direct_fallback(monkeypatch):
    monkeypatch.setattr(mcp_client, "_ENABLED", True)
    monkeypatch.setattr(mcp_client, "_get_client", lambda: SimpleNamespace(call=AsyncMock(
        return_value=ToolExecutionResult(success=False, error_type="outcome_unknown"))))
    direct = AsyncMock(side_effect=AssertionError("must not replay write"))
    monkeypatch.setattr(mcp_client.TOOL_REGISTRY["create_ticket"], "execute_with_retry", direct)
    result = await mcp_client.invoke_tool("create_ticket")
    assert not result.success and result.error_type == "outcome_unknown"
    direct.assert_not_called()


async def test_unknown_outcome_reads_committed_receipt_only(tmp_path, monkeypatch):
    from app.agent.tools import CreateTicketTool
    async with api_context(tmp_path / "receipt.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        await seed_message(factory)
        payload = arguments()
        first = await CreateTicketTool().execute(**payload, authorization=capability(payload))
        assert first["success"]
        monkeypatch.setattr(mcp_client, "_ENABLED", True)
        monkeypatch.setattr(mcp_client, "_get_client", lambda: SimpleNamespace(call=AsyncMock(
            return_value=ToolExecutionResult(success=False, error_type="outcome_unknown"))))
        direct = AsyncMock(side_effect=AssertionError("must read receipt, never execute"))
        monkeypatch.setattr(mcp_client.TOOL_REGISTRY["create_ticket"], "execute_with_retry", direct)
        result = await mcp_client.invoke_tool("create_ticket", **payload, authorization=capability(payload))
        assert result.success and result.data["ticket_id"] == first["data"]["ticket_id"]
        direct.assert_not_called()
