"""人工请求解决事务：幂等终态、座席归属断言及追加审计。"""
from app.utils.utc_time import utc_now_db
import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import HandoffRequestRecord, HandoffStatus, OperationAudit, User, UserRole
from app.services.handoff_assignment import lock_capacity_gate


def _validate_result(item: HandoffRequestRecord, resolution: str, agent_id: str | None) -> None:
    if agent_id is not None and agent_id != item.assigned_to:
        raise HTTPException(status_code=409, detail="处理座席与原指派不一致，不能通过解决接口重新分配")
    if item.status == HandoffStatus.RESOLVED and item.resolution != resolution:
        raise HTTPException(status_code=409, detail="请求已解决，处理结果不可覆盖")


async def resolve_request(db: AsyncSession, actor: User, request_id: str,
                          resolution: str, agent_id: str | None) -> dict:
    actor_id = actor.id
    actor = await db.get(User, actor_id, populate_existing=True)
    if actor is None or not actor.status or actor.disabled_at is not None or actor.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=403, detail="需要有效管理员权限")
    resolution = resolution.strip()
    if not resolution or len(resolution) > 10000:
        raise HTTPException(status_code=400, detail="处理结果须为1至10000个非空白字符")
    item = await db.get(HandoffRequestRecord, request_id, populate_existing=True)
    if item is None:
        raise HTTPException(status_code=404, detail="请求不存在")
    _validate_result(item, resolution, agent_id)
    if item.status == HandoffStatus.RESOLVED:
        return {"request_id": item.id, "resolution": item.resolution}
    original_agent = item.assigned_to
    old_status = item.status
    old_resolution = item.resolution
    old_resolved_at = item.resolved_at
    old_assigned_at = item.assigned_at
    owner_id = item.user_id
    try:
        if original_agent is not None:
            await lock_capacity_gate(db, original_agent)
        now = utc_now_db()
        result = await db.execute(update(HandoffRequestRecord).where(
            HandoffRequestRecord.id == request_id, HandoffRequestRecord.status == old_status,
            HandoffRequestRecord.assigned_to == original_agent,
            HandoffRequestRecord.assigned_at == old_assigned_at,
            HandoffRequestRecord.resolution == old_resolution,
            HandoffRequestRecord.resolved_at == old_resolved_at,
        ).values(status=HandoffStatus.RESOLVED, resolution=resolution, resolved_at=now)
            .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            # 条件写失败先结束旧事务，再重读胜者，不继续使用旧身份映射/快照。
            await db.rollback()
            latest = await db.get(HandoffRequestRecord, request_id, populate_existing=True)
            if latest is None:
                raise HTTPException(status_code=404, detail="请求不存在")
            _validate_result(latest, resolution, agent_id)
            if latest.status == HandoffStatus.RESOLVED:
                return {"request_id": latest.id, "resolution": latest.resolution}
            raise HTTPException(status_code=409, detail="人工请求已被其他操作修改，请重新读取")
        receipt = {"request_id": request_id, "resolution": resolution}
        db.add(OperationAudit(
            operation="resolve_handoff", idempotency_key=f"handoff-resolution:{uuid.uuid4()}",
            user_id=owner_id, resource_id=request_id,
            request_payload={"actor_id": actor_id, "owner_id": owner_id, "agent_id": original_agent,
                             "resolution": resolution, "old_status": old_status.value},
            result_payload={**receipt, "status": "resolved", "agent_id": original_agent,
                            "resolved_at": now.isoformat()},
        ))
        await db.commit()
        return receipt
    except Exception:
        await db.rollback()
        raise
