"""Agent 内 MCP 工具客户端 - 工具执行统一入口。

传输模式（``MCP_TRANSPORT``）：
- ``inprocess``（默认）：通过 MCP SDK 内存传输连接同进程 FastMCP server。
  无子进程开销、无跨进程 join 竞态，调用延迟最低；
- ``stdio``：拉起 ``python -m <MCP_SERVER_ENTRY>`` 子进程（stdio 传输），
  进程隔离更强，适合容器化部署时与主进程解耦。

两种模式都走标准 MCP 协议（list_tools / call_tool），工具实现与参数
仍来自 ``app.agent.tools.TOOL_REGISTRY``，行为一致；MCP 未启用、连接
失败或单次调用异常时自动回退到进程内直调。该回退保证工具执行链路
永远可用，MCP 只是“协议接入层”。
"""
import asyncio
import json
import logging
import sys
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


class _McpToolClient:
    """进程级单例 MCP 客户端（懒初始化，失败即禁用，调用以锁串行化）。"""

    def __init__(self) -> None:
        self._ctx = None
        self._session = None
        self._lock = asyncio.Lock()
        self._ready = False
        self._failed = False

    async def _ensure(self) -> bool:
        """建立连接（in-process 或 stdio 子进程）；只尝试一次，失败后标记禁用。

        连接初始化由自增锁串行化（幂等置位 _ready/_failed），避免并发首调
        同时建连竞态；后续工具调用不再持锁，MCP JSON-RPC 以 request-id 匹配
        响应、FastMCP in-process server 无共享可变状态，天然支持并发 in-flight。
        """
        if self._ready or self._failed:
            return self._ready
        async with self._lock:
            if self._ready or self._failed:  # 双检：等待锁期间可能已被其他协程建连
                return self._ready
            try:
                from mcp.shared.memory import create_connected_server_and_client_session

                if _TRANSPORT == "stdio":
                    from mcp import ClientSession, StdioServerParameters
                    from mcp.client.stdio import stdio_client

                    params = StdioServerParameters(command=sys.executable, args=["-m", _ENTRY])
                    self._ctx = stdio_client(params)
                    read_stream, write_stream = await self._ctx.__aenter__()
                    self._session = await ClientSession(read_stream, write_stream).__aenter__()
                else:  # inprocess：同进程 FastMCP server，无子进程
                    from app.agent.mcp_server import mcp as fastmcp_app

                    # create_connected_server_and_client_session 期望 MCP Server 抽象，
                    # 传入 FastMCP 底层 LowLevelServer（._mcp_server），否则缺少
                    # create_initialization_options 导致 AttributeError。
                    self._ctx = create_connected_server_and_client_session(fastmcp_app._mcp_server)
                    self._session = await self._ctx.__aenter__()

                await self._session.initialize()
                tools = await self._session.list_tools()
                self._ready = True
                logger.info("MCP client connected (transport=%s, tools=%d)", _TRANSPORT, len(tools.tools or []))
            except Exception as exc:  # MCP 初始化失败：降级直调，不阻断主链路
                logger.warning(
                    "MCP client unavailable (%s), fallback to direct tool calls: %s",
                    _TRANSPORT, exc,
                )
                self._failed = True
            return self._ready

    async def call(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[ToolExecutionResult]:
        if not _ENABLED:
            return None
        if not await self._ensure():
            return None
        try:
            # 不持调用锁：连接初始化由 _ensure 串行保证，调用阶段并发执行
            # （MCP JSON-RPC request-id 匹配响应，server 工具实现无共享可变状态）。
            res = await self._session.call_tool(tool_name, arguments)
            raw = json.loads(_extract_text_content(res)) if _extract_text_content(res) else {}
            return ToolExecutionResult.model_validate(raw)
        except Exception as exc:
            logger.warning("MCP tool call %s failed, fallback to direct call: %s", tool_name,
                           type(exc).__name__ if tool_name in {"create_ticket", "update_ticket_status", "escalate_to_human"} else exc)
            return None

    async def aclose(self) -> None:
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None
        if self._ctx is not None:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._ctx = None
        self._ready = False
        self._failed = False


_client: Optional[_McpToolClient] = None


def _get_client() -> _McpToolClient:
    global _client
    if _client is None:
        _client = _McpToolClient()
    return _client


async def invoke_tool(tool_name: str, **kwargs: Any) -> Optional[ToolExecutionResult]:
    """统一工具执行入口：MCP 协议优先，进程内直调兜底。

    返回 None 表示工具不存在（调用方按“跳过”处理），与原有行为一致。
    """
    if tool_name not in TOOL_REGISTRY:
        return None

    if _ENABLED:
        via_mcp = await _get_client().call(tool_name, dict(kwargs))
        if via_mcp is not None:
            return via_mcp

    return await TOOL_REGISTRY[tool_name].execute_with_retry(**kwargs)


async def shutdown_mcp_client() -> None:
    """应用退出时关闭连接（含子进程），避免僵尸进程。"""
    if _client is not None:
        await _client.aclose()
