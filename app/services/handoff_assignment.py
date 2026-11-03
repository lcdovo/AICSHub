"""持久座席闸门串行化分配与容量判定；名册规则仍来自现有演示配置。"""
import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import HandoffCapacityGate, HandoffRequestRecord, HandoffStatus, OperationAudit, User, UserRole
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


async def reserve_capacity(db: AsyncSession, agent: HumanAgent) -> None:
    """更新闸门取得事务写锁，随后使用current read读取有效指派。

    FOR UPDATE避免MySQL REPEATABLE READ复用本事务先前身份/请求读取的旧快照。
    SQLite由闸门UPDATE的写事务串行化；事务结束前不能提前释放或commit。
    """
    dialect = db.get_bind().dialect.name
    if dialect == "mysql":
        from sqlalchemy.dialects.mysql import insert
        statement = insert(HandoffCapacityGate).values(agent_id=agent.agent_id, revision=1)
        statement = statement.on_duplicate_key_update(revision=HandoffCapacityGate.revision + 1)
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
        statement = insert(HandoffCapacityGate).values(agent_id=agent.agent_id, revision=1)
        statement = statement.on_conflict_do_update(
            index_elements=["agent_id"], set_={"revision": HandoffCapacityGate.revision + 1})
    else:
        raise HTTPException(status_code=503, detail="当前数据库尚未支持安全座席容量协调")
    # upsert本身取得排他写锁，避免并发重复INSERT失败后的共享锁升级竞争。
    await db.execute(statement)
    ids = (await db.execute(select(HandoffRequestRecord.id).where(
        HandoffRequestRecord.assigned_to == agent.agent_id,
        HandoffRequestRecord.status == HandoffStatus.ASSIGNED,
    ).with_for_update())).scalars().all()
    if effective_load(agent, {agent.agent_id: len(ids)}) >= agent.max_load:
        raise HTTPException(status_code=409, detail="座席负载已满")


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
    if type(agent.max_load) is not int or agent.max_load <= 0:
        raise HTTPException(status_code=409, detail="座席负载已满或容量无效")
    now = datetime.utcnow()
    owner_id = item.user_id
    try:
        await reserve_capacity(db, agent)
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
