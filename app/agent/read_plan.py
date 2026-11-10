"""显式只读白名单；计划不能替代调用者身份或业务前置，写工具从不在并行计划内。"""

import asyncio
from dataclasses import dataclass
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class ReadTask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool: Literal["query_order", "get_user_history"]
    resource_id: str | None = Field(default=None, min_length=1, max_length=32)


@dataclass(frozen=True)
class ReadPrincipal:
    """仅由服务端本轮状态构造，不属于模型可输出的参数。"""
    actor_id: int
    user_id: int
    session_id: str


async def execute_read_plan(tasks: list[ReadTask], actor, reader):
    if len(tasks) > 2:
        raise ValueError("read_plan_budget_exceeded")

    # reader依赖只能由可信服务注入，参数没有user_id/代码/SQL。
    async def one(task):
        async with asyncio.timeout(5):
            return await reader(task, actor)

    pending = [asyncio.create_task(one(task)) for task in tasks]
    try:
        return await asyncio.gather(*pending)
    finally:
        # gather首次异常不会自动取消其他任务；显式回收包括父Run取消/超时。
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


async def run_authorized_reads(tasks: list[ReadTask], principal: ReadPrincipal):
    """主图注入身份和私有签名后调用同一工具入口，结果不改变Principal。"""
    from app.agent.read_authorization import authorize_read, HistoryReadInput, OrderReadInput
    from app.agent.mcp_client import invoke_tool

    async def reader(task, actor):
        if task.tool == "query_order":
            payload = OrderReadInput(user_id=actor.user_id, order_id=task.resource_id).model_dump()
        else:
            if task.resource_id is not None:
                raise ValueError("history_resource_id_not_supported")
            payload = HistoryReadInput(user_id=actor.user_id, session_id=actor.session_id).model_dump()
        result = await invoke_tool(task.tool, **payload,
                                   authorization=authorize_read(task.tool, payload, actor.actor_id))
        return payload, result

    return await execute_read_plan(tasks, principal, reader)


def summarize_reads(tasks, results, *, has_conditions=False, user_message=""):
    """只描述已取得的读结果；部分失败不能被最后一个成功结果覆盖。"""
    lines = []
    for task, (_, result) in zip(tasks, results):
        label = "订单查询" if task.tool == "query_order" else "历史咨询查询"
        if result is None or not result.success or not isinstance(result.data, dict):
            lines.append(f"{label}未完成，请核对查询对象或稍后重试。")
            continue
        data = result.data
        if task.tool == "query_order":
            # 物流查询必须复用订单模板，避免只读计划先写入旧的状态摘要，
            # 从而跳过 response_node 中的物流字段补充逻辑。
            if any(word in user_message for word in ("物流", "快递", "运单", "配送", "运输", "到哪", "发货")):
                from app.agent.nodes.response_node import build_order_reply_template
                lines.append(build_order_reply_template(data, user_message))
            else:
                lines.append(f"订单 {data.get('order_id', task.resource_id)} 当前状态：{data.get('status', '未知')}，"
                             f"订单金额：{data.get('total_amount', '未知')}。")
        else:
            # 历史是用户陈述，不能证明当前政策、订单状态或授权业务动作。
            history = data.get("history") or []
            lines.append(f"已读取本人的历史咨询，返回{len(history)}条记录；这些记录仅作历史背景。")
            for item in history[:3]:
                if isinstance(item, dict):
                    lines.append(f"历史记录：{str(item.get('summary', ''))[:200]}")
    if has_conditions:
        lines.append("本轮仅完成前置查询，条件后的申请尚未准备或提交；如需办理，请明确发起并单独确认。")
    return "\n".join(lines)
