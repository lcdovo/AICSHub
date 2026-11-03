"""工单/人工请求的原子创建与持久幂等；不使用进程锁或成功缓存。"""
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    HandoffRequestRecord, HandoffStatus, NotificationRecord, OperationAudit,
    Session, Ticket, TicketPriority, TicketStatus, User,
)
from app.security import ensure_owner_or_admin


def ticket_receipt(ticket: Ticket) -> dict[str, Any]:
    return {"ticket_id": ticket.id, "user_id": ticket.user_id, "category": ticket.category,
            "content": ticket.content, "priority": ticket.priority.value, "status": ticket.status.value,
            "assigned_to": ticket.assigned_to, "session_id": ticket.session_id,
            "sla_deadline": ticket.sla_deadline.isoformat() if ticket.sla_deadline else None,
            "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
            "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
            "resolution": ticket.resolution}


def handoff_receipt(item: HandoffRequestRecord) -> dict[str, Any]:
    return {"request_id": item.id, "user_id": item.user_id, "session_id": item.session_id,
            "reason": item.reason, "priority": item.priority, "context": item.context or {},
            "status": item.status.value, "assigned_to": item.assigned_to, "resolution": item.resolution,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "assigned_at": item.assigned_at.isoformat() if item.assigned_at else None,
            "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
            "sla_deadline": item.sla_deadline.isoformat() if item.sla_deadline else None}


def handoff_message(data: dict[str, Any]) -> str:
    request_id = data.get("request_id") or data.get("escalation_id")
    labels = {"pending": "待处理", "assigned": "已分配", "resolved": "已解决", "cancelled": "已取消"}
    if (not isinstance(request_id, str) or not request_id.strip()
            or not isinstance(data.get("status"), str) or data["status"] not in labels):
        return "暂时无法确认转人工请求结果，请查看请求记录或联系支持。"
    return f"转人工请求 {request_id} 已登记，当前状态：{labels[data['status']]}。"


def resolve_idempotency_key(body_key: str | None, header_key: str | None) -> str | None:
    for key in (body_key, header_key):
        if key is not None and (not key.strip() or len(key) > 128):
            raise HTTPException(status_code=400, detail="幂等键须为1至128个字符且不能全为空白")
    if body_key is not None and header_key is not None and body_key != header_key:
        raise HTTPException(status_code=400, detail="请求体与请求头的幂等键不一致")
    return body_key if body_key is not None else header_key


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


async def _load_receipt(db: AsyncSession, key: str) -> OperationAudit | None:
    return await db.scalar(select(OperationAudit).where(OperationAudit.idempotency_key == key))


async def _replay(
    db: AsyncSession, audit: OperationAudit, operation: str, actor_id: int,
    owner_id: int, request_hash: str,
) -> Ticket | HandoffRequestRecord:
    saved = audit.request_payload or {}
    if (audit.operation != operation or audit.user_id != owner_id or audit.status != "succeeded"
            or saved.get("version") != 1 or saved.get("actor_id") != actor_id
            or saved.get("request_hash") != request_hash):
        raise HTTPException(status_code=409, detail="幂等键已用于不同请求")
    model = Ticket if operation == "create_ticket" else HandoffRequestRecord
    item = await db.get(model, audit.resource_id)
    if item is None or item.user_id != owner_id:
        raise HTTPException(status_code=409, detail="原请求资源不可重放，请联系管理员核查")
    return item


async def create_request(
    db: AsyncSession, actor: User, operation: str, payload: dict[str, Any], key: str | None,
) -> tuple[Ticket | HandoffRequestRecord, bool]:
    """提交资源、通知、审计到同一事务；唯一约束竞争失败后重读胜者。

    payload 为经过请求schema解析并补全默认值的业务字段，不含客户端幂等键。
    本服务负责本请求事务，不在调用方已有业务写入中嵌套使用。
    """
    if operation not in {"create_ticket", "request_handoff"}:
        raise ValueError("unsupported ticket operation")
    owner_id = payload["user_id"]
    ensure_owner_or_admin(actor, owner_id)
    # rollback 会使同一Session中的认证User过期，竞争恢复路径不能再隐式读取actor ORM属性。
    actor_id = actor.id
    scoped_key = "ticket-v1:" + _digest([operation, actor_id, owner_id, key]) if key is not None else None
    try:
        request_hash = _digest(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="请求内容不是有效的JSON数据") from exc
    if scoped_key:
        existing = await _load_receipt(db, scoped_key)
        if existing is not None:
            return await _replay(db, existing, operation, actor_id, owner_id, request_hash), True
        # 旧键没有可信actor。保留旧记录并明确冲突，不能重执行或猜测历史授权。
        model = Ticket if operation == "create_ticket" else HandoffRequestRecord
        legacy = await db.scalar(select(model.id).where(model.idempotency_key == key))
        legacy_audit = await _load_receipt(db, key)
        if legacy is not None or legacy_audit is not None:
            raise HTTPException(status_code=409, detail="旧版幂等记录无法安全重放，请核查原请求")

    session_id = payload.get("session_id")
    if session_id is not None:
        session = await db.get(Session, session_id)
        if session is None or session.user_id != owner_id:
            raise HTTPException(status_code=404, detail="会话不存在")
    if await db.get(User, owner_id) is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    resource_id = str(uuid.uuid4())
    if operation == "create_ticket":
        item = Ticket(
            id=resource_id, user_id=owner_id, category=payload["category"], content=payload["content"],
            priority=TicketPriority(payload["priority"]), session_id=session_id,
            status=TicketStatus.PENDING, idempotency_key=scoped_key,
        )
        event_type, result_field = "ticket.created", "ticket_id"
    else:
        now = datetime.utcnow()
        hours = {"urgent": 1, "high": 4, "normal": 24, "low": 48}[payload["priority"]]
        item = HandoffRequestRecord(
            id=resource_id, **payload, status=HandoffStatus.PENDING,
            sla_deadline=now + timedelta(hours=hours), idempotency_key=scoped_key,
        )
        event_type, result_field = "handoff.created", "request_id"
    audit = OperationAudit(
        operation=operation, user_id=owner_id, resource_id=resource_id,
        idempotency_key=scoped_key or f"{operation}:{resource_id}",
        request_payload={"version": 1, "actor_id": actor_id, "owner_id": owner_id,
                         "request_hash": request_hash, "request": payload},
        result_payload={result_field: resource_id},
    )
    try:
        # 先争取审计键，只有胜者创建资源/事件；审计尚未提交，失败会整体回滚。
        db.add(audit)
        await db.flush()
        db.add(item)
        notification_id = str(uuid.uuid4())
        db.add(NotificationRecord(
            id=notification_id, event_type=event_type, recipient=str(owner_id),
            payload={result_field: resource_id}, idempotency_key=f"{event_type}:{resource_id}",
        ))
        from app.services.background_jobs import enqueue
        enqueue(db,"notification",notification_id,owner_id,"notification:"+notification_id)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if scoped_key:
            winner = await _load_receipt(db, scoped_key)
            if winner is not None:
                return await _replay(db, winner, operation, actor_id, owner_id, request_hash), True
        raise HTTPException(status_code=409, detail="创建请求冲突，请核查请求参数") from None
    return item, False
