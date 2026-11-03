"""显式只读白名单；计划不能替代调用者身份或业务前置，写工具从不在并行计划内。"""

import asyncio
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class ReadTask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool: Literal["query_order", "get_user_history"]
    resource_id: str | None = Field(default=None, max_length=80)


async def execute_read_plan(tasks: list[ReadTask], actor, reader):
    if len(tasks) > 2:
        raise ValueError("read_plan_budget_exceeded")

    # reader依赖只能由可信服务注入，参数没有user_id/代码/SQL。
    async def one(task):
        async with asyncio.timeout(5):
            return await reader(task, actor)

    return await asyncio.gather(*(one(task) for task in tasks))
