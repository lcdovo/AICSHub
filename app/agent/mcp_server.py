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
from pydantic import StrictInt, StrictStr

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


async def _invoke(tool_name: StrictStr, **kwargs: Any) -> Dict[str, Any]:
    """统一执行入口：复用注册表的重试/熔断/校验契约。"""
    tool = TOOL_REGISTRY.get(tool_name)
    if tool is None:
        return ToolExecutionResult(
            success=False, error=f"工具 {tool_name} 不存在", error_type="not_found"
        ).model_dump()
    result: ToolExecutionResult = await tool.execute_with_retry(**kwargs)
    return result.model_dump()


@mcp.tool(name="query_order", description="根据订单号查询订单状态、物流信息、订单详情")
async def query_order(order_id: StrictStr, user_id: StrictInt = 0, authorization: StrictStr = "") -> Dict[str, Any]:
    return await _invoke("query_order", order_id=order_id, user_id=user_id, authorization=authorization)


@mcp.tool(name="create_ticket", description="创建客服工单，用于处理用户投诉、咨询等问题")
async def create_ticket(user_id: StrictInt, category: StrictStr, content: StrictStr, priority: StrictStr = "medium",
                        session_id: StrictStr = "", idempotency_key: StrictStr = "", authorization: StrictStr = "") -> Dict[str, Any]:
    return await _invoke(
        "create_ticket", user_id=user_id, category=category, content=content, priority=priority,
        session_id=session_id, idempotency_key=idempotency_key, authorization=authorization,
    )


@mcp.tool(name="apply_refund", description="登记模拟待审核退换货申请，需要本轮确认与执行凭据；不执行真实支付")
async def apply_refund(
    order_id: StrictStr,
    reason: StrictStr,
    type: StrictStr = "refund",  # noqa: A002 - 与既有工具参数名保持一致
    user_id: StrictInt = 0,
    idempotency_key: StrictStr = "",
    session_id: StrictStr = "",
    authorization: StrictStr = "",
    action_id: StrictStr | None = None,
    action_version: StrictInt | None = None,
) -> Dict[str, Any]:
    return await _invoke(
        "apply_refund",
        order_id=order_id,
        reason=reason,
        type=type,
        user_id=user_id,
        idempotency_key=idempotency_key,
        session_id=session_id,
        authorization=authorization,
        **({"action_id": action_id, "action_version": action_version} if action_id is not None else {}),
    )


@mcp.tool(name="search_kb", description="从知识库中检索相关问题的答案，支持关键词匹配和语义搜索")
async def search_kb(query: StrictStr, top_k: StrictInt = 3) -> Dict[str, Any]:
    return await _invoke("search_kb", query=query, top_k=top_k)


@mcp.tool(name="escalate_to_human", description="登记可查询的人工处理请求；登记不代表已接通客服")
async def escalate_to_human(
    reason: StrictStr,
    priority: StrictStr = "normal",
    context: StrictStr = "",
    user_id: StrictInt = 0,
    session_id: StrictStr = "",
    idempotency_key: StrictStr = "",
    authorization: StrictStr = "",
) -> Dict[str, Any]:
    return await _invoke(
        "escalate_to_human",
        reason=reason,
        priority=priority,
        context=context,
        user_id=user_id,
        session_id=session_id,
        idempotency_key=idempotency_key,
        authorization=authorization,
    )


@mcp.tool(name="send_notification", description="通知发送入口；当前未接入投递适配器，返回能力不可用")
async def send_notification(channel: StrictStr, content: StrictStr, user_id: StrictInt = 0) -> Dict[str, Any]:
    return await _invoke("send_notification", channel=channel, content=content, user_id=user_id)


@mcp.tool(name="update_ticket_status", description="更新工单的处理状态，用于工单流转和状态同步")
async def update_ticket_status(ticket_id: StrictStr, status: StrictStr, note: StrictStr = "",
                               idempotency_key: StrictStr = "", authorization: StrictStr = "") -> Dict[str, Any]:
    return await _invoke("update_ticket_status", ticket_id=ticket_id, status=status, note=note,
                         idempotency_key=idempotency_key, authorization=authorization)


@mcp.tool(name="get_user_history", description="获取用户的历史咨询记录、工单历史、偏好标签等（跨会话长期记忆/用户画像）")
async def get_user_history(user_id: StrictInt, limit: StrictInt = 10, query: StrictStr = "", session_id: StrictStr = "", authorization: StrictStr = "") -> Dict[str, Any]:
    return await _invoke("get_user_history", user_id=user_id, limit=limit, query=query, session_id=session_id, authorization=authorization)


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
