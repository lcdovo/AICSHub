"""MCP Server - 将客服企业工具以 MCP 协议暴露。

基于 FastMCP 定义 8 个客服工具，实现与参数元数据全部复用
``app.agent.tools.TOOL_REGISTRY``，避免双份定义漂移。

以 stdio 传输运行（普通进程）::

    python -m app.agent.mcp_server

可被任意 MCP Host（Claude Desktop / 兼容 IDE / 其他 Agent）加载复用；
本项目 agent 内部则通过 ``app.agent.mcp_client`` 调用该 server
（MCP 不可用时自动回退到进程内直调）。

工单创建需要服务端签发的绑定轮次凭据、session_id和幂等键；裸user_id
不构成写入授权。其他工具仍按各自执行上下文与能力边界处理。
"""
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

from fastmcp import FastMCP  # noqa: E402

from app.agent.tools import TOOL_REGISTRY, ToolExecutionResult  # noqa: E402

mcp = FastMCP(
    "customer-service-tools",
    instructions=(
        "智能客服企业级工具集：订单查询、工单创建、退换货申请、知识库检索、"
        "转人工、通知发送、工单状态更新、用户画像查询。"
        "工具按各自契约校验上下文、幂等与重试；工单创建需要内部执行凭据。失败时返回 error/hint 字段。"
    ),
)


async def _invoke(tool_name: str, **kwargs: Any) -> Dict[str, Any]:
    """统一执行入口：复用注册表的重试/熔断/校验契约。"""
    tool = TOOL_REGISTRY.get(tool_name)
    if tool is None:
        return ToolExecutionResult(
            success=False, error=f"工具 {tool_name} 不存在", error_type="not_found"
        ).model_dump()
    result: ToolExecutionResult = await tool.execute_with_retry(**kwargs)
    return result.model_dump()


@mcp.tool(name="query_order", description="根据订单号查询订单状态、物流信息、订单详情")
async def query_order(order_id: str, user_id: int = 0) -> Dict[str, Any]:
    return await _invoke("query_order", order_id=order_id, user_id=user_id)


@mcp.tool(name="create_ticket", description="创建客服工单，用于处理用户投诉、咨询等问题")
async def create_ticket(user_id: int, category: str, content: str, priority: str = "medium",
                        session_id: str = "", idempotency_key: str = "", authorization: str = "") -> Dict[str, Any]:
    return await _invoke(
        "create_ticket", user_id=user_id, category=category, content=content, priority=priority,
        session_id=session_id, idempotency_key=idempotency_key, authorization=authorization,
    )


@mcp.tool(name="apply_refund", description="申请退换货，需校验订单状态是否满足退换货条件")
async def apply_refund(
    order_id: str,
    reason: str,
    type: str = "refund",  # noqa: A002 - 与既有工具参数名保持一致
    user_id: int = 0,
    idempotency_key: str = "",
) -> Dict[str, Any]:
    return await _invoke(
        "apply_refund",
        order_id=order_id,
        reason=reason,
        type=type,
        user_id=user_id,
        idempotency_key=idempotency_key,
    )


@mcp.tool(name="search_kb", description="从知识库中检索相关问题的答案，支持关键词匹配和语义搜索")
async def search_kb(query: str, top_k: int = 3) -> Dict[str, Any]:
    return await _invoke("search_kb", query=query, top_k=top_k)


@mcp.tool(name="escalate_to_human", description="将用户转接至人工客服处理，支持选择优先级和转接原因")
async def escalate_to_human(
    reason: str,
    priority: str = "normal",
    context: str = "",
    user_id: int = 0,
    session_id: str = "",
) -> Dict[str, Any]:
    return await _invoke(
        "escalate_to_human",
        reason=reason,
        priority=priority,
        context=context,
        user_id=user_id,
        session_id=session_id,
    )


@mcp.tool(name="send_notification", description="向用户发送短信或站内信通知")
async def send_notification(channel: str, content: str, user_id: int = 0) -> Dict[str, Any]:
    return await _invoke("send_notification", channel=channel, content=content, user_id=user_id)


@mcp.tool(name="update_ticket_status", description="更新工单的处理状态，用于工单流转和状态同步")
async def update_ticket_status(ticket_id: str, status: str, note: str = "",
                               idempotency_key: str = "", authorization: str = "") -> Dict[str, Any]:
    return await _invoke("update_ticket_status", ticket_id=ticket_id, status=status, note=note,
                         idempotency_key=idempotency_key, authorization=authorization)


@mcp.tool(name="get_user_history", description="获取用户的历史咨询记录、工单历史、偏好标签等（跨会话长期记忆/用户画像）")
async def get_user_history(user_id: int, limit: int = 10, query: str = "") -> Dict[str, Any]:
    return await _invoke("get_user_history", user_id=user_id, limit=limit, query=query)


def main() -> None:
    """以 stdio 传输运行 MCP server（阻塞），供 `python -m app.agent.mcp_server` 启动。

    独立进程运行才需要接管标准输出；agent 进程内通过 in-process 传输连接时
    不调用本入口，避免模块级 basicConfig 干扰主进程日志级别。
    """
    import logging

    logging.basicConfig(level=logging.WARNING)
    mcp.run()


if __name__ == "__main__":
    main()
