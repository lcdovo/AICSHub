"""工单更新共享事务：管理员授权、既有状态机、CAS与操作回执。"""
import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import OperationAudit, Ticket, TicketPriority, TicketStatus, User, UserRole
from app.services.ticket_creation import resolve_idempotency_key, ticket_receipt


ALLOWED_TRANSITIONS = {
    TicketStatus.PENDING: {TicketStatus.PROCESSING, TicketStatus.ESCALATED},
    TicketStatus.PROCESSING: {TicketStatus.RESOLVED, TicketStatus.ESCALATED},
    TicketStatus.RESOLVED: {TicketStatus.CLOSED},
    TicketStatus.ESCALATED: {TicketStatus.PROCESSING, TicketStatus.RESOLVED},
    TicketStatus.CLOSED: set(),
}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


async def _admin(db: AsyncSession, actor_id: int) -> User:
    actor = await db.get(User, actor_id, populate_existing=True)
    if actor is None or not actor.status or actor.disabled_at is not None:
        raise HTTPException(status_code=403, detail="操作人已禁用或不存在")
    if actor.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return actor


async def _existing(db: AsyncSession, key: str) -> OperationAudit | None:
    return await db.scalar(select(OperationAudit).where(OperationAudit.idempotency_key == key))


def _replay(audit: OperationAudit, actor_id: int, ticket_id: str, digest: str) -> dict:
    saved = audit.request_payload or {}
    if (audit.operation != "update_ticket_status" or audit.resource_id != ticket_id
            or audit.status != "succeeded" or saved.get("actor_id") != actor_id
            or saved.get("request_hash") != digest or not audit.result_payload):
        raise HTTPException(status_code=409, detail="幂等键已用于不同更新请求")
    return audit.result_payload


async def update_ticket(
    db: AsyncSession, actor: User, ticket_id: str, changes: dict[str, Any],
    *, note: str = "", key: str | None = None,
) -> dict[str, Any]:
    actor_id = actor.id
    await _admin(db, actor_id)
    resolve_idempotency_key(key, None)
    if set(changes) - {"status", "priority", "assigned_to", "resolution"}:
        raise HTTPException(status_code=400, detail="不支持的工单更新字段")
    digest = _digest({"changes": changes, "note": note})
    scoped_key = "ticket-update-v1:" + _digest([actor_id, ticket_id, key]) if key is not None else None
    if scoped_key:
        existing = await _existing(db, scoped_key)
        if existing is not None:
            return _replay(existing, actor_id, ticket_id, digest)
    ticket = await db.get(Ticket, ticket_id, populate_existing=True)
    if ticket is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    old_status = ticket.status
    # 除状态外也比较原可编辑字段，避免同秒时间精度下覆盖另一管理员的编辑。
    original = {field: getattr(ticket, field) for field in (
        "status", "priority", "assigned_to", "resolution", "resolved_at", "updated_at", "user_id"
    )}
    values: dict[str, Any] = {"updated_at": datetime.utcnow()}
    if changes.get("status"):
        try:
            target = TicketStatus(changes["status"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="工单状态无效") from exc
        if target != old_status and target not in ALLOWED_TRANSITIONS.get(old_status, set()):
            raise HTTPException(status_code=409, detail=f"不允许从 {old_status.value} 转换到 {target.value}")
        values["status"] = target
        if target in {TicketStatus.RESOLVED, TicketStatus.CLOSED}:
            values["resolved_at"] = datetime.utcnow()
    if changes.get("priority"):
        try:
            values["priority"] = TicketPriority(changes["priority"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="工单优先级无效") from exc
    for field in ("assigned_to", "resolution"):
        if changes.get(field) is not None:
            values[field] = changes[field]
    audit = OperationAudit(
        operation="update_ticket_status", user_id=ticket.user_id, resource_id=ticket_id,
        idempotency_key=scoped_key or f"ticket-update:{uuid.uuid4()}",
        request_payload={"version": 1, "actor_id": actor_id, "owner_id": ticket.user_id,
                         "request_hash": digest, "changes": changes, "note": note},
    )
    try:
        db.add(audit)
        await db.flush()
        result = await db.execute(update(Ticket).where(
            Ticket.id == ticket_id, *[getattr(Ticket, field) == value for field, value in original.items()]
        ).values(**values).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise HTTPException(status_code=409, detail="工单已被其他请求修改，请重新读取")
        await db.refresh(ticket)
        receipt = {"ticket": ticket_receipt(ticket), "old_status": old_status.value,
                   "new_status": ticket.status.value, "note": note}
        audit.result_payload = receipt
        await db.commit()
        return receipt
    except IntegrityError:
        await db.rollback()
        await _admin(db, actor_id)
        if scoped_key:
            winner = await _existing(db, scoped_key)
            if winner is not None:
                return _replay(winner, actor_id, ticket_id, digest)
        raise HTTPException(status_code=409, detail="工单更新冲突，请核查原请求") from None
    except Exception:
        await db.rollback()
        raise
