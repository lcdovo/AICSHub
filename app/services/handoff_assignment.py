"""现有演示座席的分配准入；容量跨worker竞争需后续持久名册协调。"""
import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import HandoffRequestRecord, HandoffStatus, OperationAudit, User, UserRole
from app.services.collaboration import HumanAgent, HumanAgentService


async def assigned_loads(db: AsyncSession) -> dict[str, int]:
    rows = await db.execute(select(HandoffRequestRecord.assigned_to, func.count()).where(
        HandoffRequestRecord.status == HandoffStatus.ASSIGNED,
        HandoffRequestRecord.assigned_to.is_not(None),
    ).group_by(HandoffRequestRecord.assigned_to))
    return {agent_id: count for agent_id, count in rows}


def effective_load(agent: HumanAgent, loads: dict[str, int]) -> int:
    # 两个集合无法去重，取较大值作为准入下界；不相加夸大，不以旧内存0覆盖持久指派。
    return max(agent.current_load, loads.get(agent.agent_id, 0))


async def assign_request(db: AsyncSession, actor: User, request_id: str, agent_id: str,
                         agents: HumanAgentService) -> None:
    actor_id = actor.id
    actor = await db.get(User, actor_id, populate_existing=True)
    if actor is None or not actor.status or actor.disabled_at is not None or actor.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=403, detail="需要有效管理员权限")
    item = await db.get(HandoffRequestRecord, request_id, populate_existing=True)
    if item is None:
        raise HTTPException(status_code=404, detail="转人工请求不存在")
    if item.status != HandoffStatus.PENDING:
        raise HTTPException(status_code=409, detail="仅待处理请求可分配")
    agent = agents.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="座席不存在")
    if agent.status != "online":
        raise HTTPException(status_code=409, detail="座席当前不可分配")
    if type(agent.max_load) is not int or agent.max_load <= 0 or effective_load(agent, await assigned_loads(db)) >= agent.max_load:
        raise HTTPException(status_code=409, detail="座席负载已满或容量无效")
    now = datetime.utcnow()
    owner_id = item.user_id
    try:
        result = await db.execute(update(HandoffRequestRecord).where(
            HandoffRequestRecord.id == request_id,
            HandoffRequestRecord.status == HandoffStatus.PENDING,
            HandoffRequestRecord.assigned_to == item.assigned_to,
            HandoffRequestRecord.assigned_at == item.assigned_at,
        ).values(status=HandoffStatus.ASSIGNED, assigned_to=agent_id, assigned_at=now)
            .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise HTTPException(status_code=409, detail="请求已被其他操作修改，请重新读取")
        db.add(OperationAudit(
            operation="assign_handoff", idempotency_key=f"handoff-assignment:{uuid.uuid4()}",
            user_id=owner_id, resource_id=request_id,
            request_payload={"actor_id": actor_id, "owner_id": owner_id, "agent_id": agent_id},
            result_payload={"request_id": request_id, "agent_id": agent_id, "status": "assigned",
                            "assigned_at": now.isoformat()},
        ))
        await db.commit()
    except Exception:
        await db.rollback()
        raise
