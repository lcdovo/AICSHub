"""Agent 内 MCP 工具客户端 - 工具执行统一入口。

传输模式（``MCP_TRANSPORT``）：
- ``inprocess``（默认）：通过 MCP SDK 内存传输连接同进程 FastMCP server。
  无子进程开销、无跨进程 join 竞态，调用延迟最低；
- ``stdio``：拉起 ``python -m <MCP_SERVER_ENTRY>`` 子进程（stdio 传输），
  进程隔离更强，适合容器化部署时与主进程解耦。

两种模式都走标准 MCP 协议（list_tools / call_tool），工具实现与参数
仍来自 ``app.agent.tools.TOOL_REGISTRY``。派发前连接失败可进程内回退；
写调用派发后异常只查持久回执，无法确定时返回unknown，不能盲目重做。
"""
import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from contextvars import Context
from typing import Any, Dict, Optional

from app.agent.tools import TOOL_REGISTRY, ToolExecutionResult
from app.config.config import settings

logger = logging.getLogger(__name__)

_ENABLED = bool(getattr(settings, "MCP_ENABLED", True))
_ENTRY = getattr(settings, "MCP_SERVER_ENTRY", "app.agent.mcp_server")
_TRANSPORT = str(getattr(settings, "MCP_TRANSPORT", "inprocess")).lower()


def _extract_text_content(result: Any) -> str:
    """从 MCP call_tool 响应中拼接 text 内容。"""
    return "".join(
        getattr(item, "text", "")
        for item in (getattr(result, "content", None) or [])
        if getattr(item, "type", "") == "text"
    )


@asynccontextmanager
async def _inprocess_session():
    """Use FastMCP's public transport; its context owns protocol initialization."""
    from fastmcp import Client
    from app.agent.mcp_server import mcp as server

    class Session:
        def __init__(self, client):
            self.client = client

        async def initialize(self):
            # Client.__aenter__ already negotiated the protocol (including v2 discover).
            return None

        async def list_tools(self):
            return await self.client.list_tools()

        async def call_tool(self, name, arguments):
            return await self.client.call_tool_mcp(name, arguments)

    async with Client(server) as client:
        yield Session(client)


class _McpToolClient:
    """由同一后台协程持有连接生命周期，失败后有界退避重连。"""

    connect_timeout = 2.
    call_timeout = 5.
    retry_base = 1.

    def __init__(self) -> None:
        self._session = None
        self._lock = asyncio.Lock()
        self._ready = False
        self._retry_at = 0.
        self._failures = 0
        self._owner = None
        self._started = asyncio.Event()
        self._stop = asyncio.Event()

    async def _connect(self) -> None:
        # AnyIO contexts must be entered and exited by the same task.
        from contextlib import AsyncExitStack
        try:
            async with AsyncExitStack() as stack:
                async with asyncio.timeout(self.connect_timeout) as budget:
                    if _TRANSPORT == "stdio":
                        from mcp import ClientSession, StdioServerParameters
                        from mcp.client.stdio import stdio_client
                        streams = await stack.enter_async_context(stdio_client(
                            StdioServerParameters(command=sys.executable, args=["-m", _ENTRY])))
                        session = await stack.enter_async_context(ClientSession(*streams))
                    else:
                        session = await stack.enter_async_context(_inprocess_session())
                    await session.initialize()
                    await session.list_tools()
                if budget.expired():
                    raise TimeoutError("late_mcp_connection")
                self._session, self._ready = session, True
                self._started.set()
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("MCP connection unavailable: %s", type(exc).__name__)
        finally:
            self._session, self._ready = None, False
            self._failures += 1
            self._retry_at = asyncio.get_running_loop().time() + min(30., self.retry_base * 2 ** min(self._failures - 1, 5))
            self._started.set()

    async def _ensure(self) -> bool:
        if self._ready:
            return True
        async with self._lock:
            if self._ready:
                return True
            if asyncio.get_running_loop().time() < self._retry_at:
                return False
            if self._owner is None or self._owner.done():
                self._started, self._stop = asyncio.Event(), asyncio.Event()
                # The persistent protocol server must not retain its first caller's
                # identity, model budget, Run journal or task ContextVars.
                self._owner = asyncio.create_task(self._connect(), context=Context())
            started = self._started
        try:
            async with asyncio.timeout(self.connect_timeout + .1):
                await started.wait()
        except TimeoutError:
            self._stop.set()
            return False
        return self._ready

    async def call(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[ToolExecutionResult]:
        if not _ENABLED or not await self._ensure():
            return None
        try:
            async with asyncio.timeout(self.call_timeout) as budget:
                res = await self._session.call_tool(tool_name, arguments)
            if budget.expired():
                raise TimeoutError("late_mcp_result")
            if getattr(res, "is_error", getattr(res, "isError", False)):
                raise ValueError("mcp_error_response")
            raw = json.loads(_extract_text_content(res)) if _extract_text_content(res) else {}
            result = ToolExecutionResult.model_validate(raw)
            self._failures = 0
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("MCP tool %s unavailable: %s", tool_name, type(exc).__name__)
            self._ready = False
            self._stop.set()
            from app.agent.capabilities import CAPABILITIES
            if CAPABILITIES[tool_name].effect == "write":
                return ToolExecutionResult(success=False, error="工具执行结果尚不确定，请核查原请求", error_type="outcome_unknown")
            return None

    async def aclose(self) -> None:
        self._stop.set()
        if self._owner and not self._owner.done():
            try:
                async with asyncio.timeout(2.):
                    await asyncio.shield(self._owner)
            except TimeoutError:
                self._owner.cancel()
                await asyncio.gather(self._owner, return_exceptions=True)
        self._owner, self._session, self._ready = None, None, False


_client: Optional[_McpToolClient] = None


def _get_client() -> _McpToolClient:
    global _client
    if _client is None:
        _client = _McpToolClient()
    return _client


async def warmup_mcp_client() -> bool:
    """Load protocol dependencies during startup, outside the per-call deadline."""
    if not _ENABLED:
        return False
    try:
        if _TRANSPORT == "inprocess":
            import importlib
            async with asyncio.timeout(10):
                await asyncio.to_thread(importlib.import_module, "app.agent.mcp_server")
        return await _get_client()._ensure()
    except Exception as exc:
        logger.warning("MCP startup unavailable: %s", type(exc).__name__)
        return False


async def invoke_tool(tool_name: str, **kwargs: Any) -> Optional[ToolExecutionResult]:
    """共享能力契约、总截止及结果校验；未知写结果只对账。"""
    from datetime import datetime, timezone
    import uuid
    from app.agent.capabilities import CAPABILITIES, output_issue
    if tool_name not in TOOL_REGISTRY or tool_name not in CAPABILITIES:
        return ToolExecutionResult(success=False, error="工具不存在", error_type="not_found")
    spec = CAPABILITIES[tool_name]
    from app.services.capability_journal import JOURNAL
    journal = JOURNAL.get()
    call_id = await journal.begin(tool_name, dict(kwargs), spec.effect) if journal else str(uuid.uuid4())
    transport = "mcp" if _ENABLED else "direct"
    try:
        async with asyncio.timeout(spec.timeout_seconds) as budget:
            result = await _get_client().call(tool_name, dict(kwargs)) if _ENABLED else None
            if result is not None:
                transport = "mcp"
            else:
                transport = "direct"
                result = await TOOL_REGISTRY[tool_name].execute_with_retry(**kwargs)
        if budget.expired():
            raise TimeoutError("late_tool_result")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        result = ToolExecutionResult(success=False, error="工具调用未完成，请按原请求核查",
            error_type="outcome_unknown" if spec.effect == "write" else "timeout" if isinstance(exc, TimeoutError) else "execution")
    unknown_errors = {"outcome_unknown", "timeout", "execution", "unknown", "dependency_unavailable", "unavailable"}
    if spec.effect == "write" and result.error_type in unknown_errors:
        from app.agent.receipt_recovery import recover
        try:
            async with asyncio.timeout(1.):
                recovered = await recover(tool_name, dict(kwargs))
            if recovered is not None:
                result, transport = recovered, "receipt"
        except (TimeoutError, ValueError):
            pass
    if result.success:
        issue = output_issue(tool_name, result.data, kwargs)
        if issue:
            result = ToolExecutionResult(success=False, error="工具返回结果未通过契约校验",
                error_type="unknown" if spec.effect == "write" else "invalid_output")
    outcome = ("succeeded" if result.success else "unknown" if spec.effect == "write" and result.error_type in unknown_errors else "rejected" if result.error_type in {
        "validation", "permission", "business", "conflict"} else "failed")
    result = result.model_copy(update={"call_id": call_id, "observed_at": datetime.now(timezone.utc).isoformat(),
                                       "transport": transport, "outcome": outcome})
    if journal:
        receipt = result.data.get(spec.receipt_key) if spec.receipt_key and isinstance(result.data, dict) else None
        await journal.finish(call_id, outcome, result.data, error=result.error_type, resource_id=receipt)
    return result


def _validate_receipt(tool_name: str, result: ToolExecutionResult) -> ToolExecutionResult:
    from app.agent.capabilities import output_issue
    if result.success and output_issue(tool_name, result.data, {}):
        return ToolExecutionResult(success=False, error="工具结果缺少可信回执", error_type="unknown")
    return result


async def shutdown_mcp_client() -> None:
    """应用退出时关闭连接（含子进程），避免僵尸进程。"""
    if _client is not None:
        await _client.aclose()
