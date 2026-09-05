"""
工单管理与人机协同 API - Phase 4 实现
"""
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends, Header
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.utils.database import get_db
from app.models.models import Ticket, TicketStatus, TicketPriority, User, UserRole, Session, HandoffRequestRecord, HandoffStatus, NotificationRecord, NotificationStatus, OperationAudit
from app.services.collaboration import get_collaboration_service
from app.utils.tracking import get_tracer
from app.security import get_current_user, ensure_owner_or_admin, require_admin

router = APIRouter(prefix="/api/v1", tags=["工单管理"])


class TicketCreateRequest(BaseModel):
    user_id: int
    category: str
    content: str
    priority: str = "medium"
    session_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class TicketUpdateRequest(BaseModel):
    status: Optional[str] = None
    assigned_to: Optional[str] = None
    resolution: Optional[str] = None
    priority: Optional[str] = None


class HandoffRequest(BaseModel):
    user_id: int
    session_id: str
    reason: str
    priority: str = "normal"
    context: Optional[dict] = None
    idempotency_key: Optional[str] = None


_ALLOWED_TRANSITIONS = {
    TicketStatus.PENDING: {TicketStatus.PROCESSING, TicketStatus.ESCALATED},
    TicketStatus.PROCESSING: {TicketStatus.RESOLVED, TicketStatus.ESCALATED},
    TicketStatus.RESOLVED: {TicketStatus.CLOSED},
    TicketStatus.ESCALATED: {TicketStatus.PROCESSING, TicketStatus.RESOLVED},
    TicketStatus.CLOSED: set(),
}


def _ticket_dict(ticket: Ticket) -> dict:
    return {"ticket_id": ticket.id, "user_id": ticket.user_id, "category": ticket.category,
            "content": ticket.content, "priority": ticket.priority.value, "status": ticket.status.value,
            "assigned_to": ticket.assigned_to, "session_id": ticket.session_id,
            "sla_deadline": ticket.sla_deadline.isoformat() if ticket.sla_deadline else None,
            "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
            "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
            "resolution": ticket.resolution}


def _handoff_dict(item: HandoffRequestRecord) -> dict:
    return {"request_id": item.id, "user_id": item.user_id, "session_id": item.session_id,
            "reason": item.reason, "priority": item.priority, "context": item.context or {},
            "status": item.status.value, "assigned_to": item.assigned_to,
            "resolution": item.resolution,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "assigned_at": item.assigned_at.isoformat() if item.assigned_at else None,
            "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
            "sla_deadline": item.sla_deadline.isoformat() if item.sla_deadline else None}


@router.post("/tickets")
async def create_ticket(
    request: TicketCreateRequest,
    db: AsyncSession = Depends(get_db),
    idempotency_header: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
):
    """创建工单"""
    ensure_owner_or_admin(current_user, request.user_id)
    idempotency_key = request.idempotency_key or idempotency_header
    if idempotency_key:
        existing = await db.scalar(select(Ticket).where(Ticket.idempotency_key == idempotency_key))
        if existing:
            return {"code": 0, "message": "幂等请求，返回已有工单", "data": _ticket_dict(existing)}
    try:
        priority = TicketPriority(request.priority)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="工单优先级无效") from exc
    ticket = Ticket(id=str(uuid.uuid4()), user_id=request.user_id, category=request.category,
                    content=request.content, priority=priority, status=TicketStatus.PENDING,
                    session_id=request.session_id, idempotency_key=idempotency_key)
    db.add(ticket)
    await db.flush()
    db.add(NotificationRecord(id=str(uuid.uuid4()), event_type="ticket.created", recipient=str(request.user_id),
                              payload={"ticket_id": ticket.id}, idempotency_key=f"ticket-created:{ticket.id}"))
    db.add(OperationAudit(operation="create_ticket", idempotency_key=idempotency_key or f"ticket:{ticket.id}",
                          user_id=request.user_id, resource_id=ticket.id,
                          request_payload=request.model_dump(), result_payload={"ticket_id": ticket.id}))
    await db.commit()

    return {
        "code": 0,
        "message": "创建成功",
        "data": _ticket_dict(ticket),
    }


@router.get("/tickets")
async def list_tickets(
    status: Optional[str] = Query(default=None, description="工单状态"),
    priority: Optional[str] = Query(default=None, description="优先级"),
    category: Optional[str] = Query(default=None, description="分类"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取工单列表"""
    query = select(Ticket)
    if current_user.role != UserRole.ADMIN.value:
        query = query.where(Ticket.user_id == current_user.id)
    if status:
        try: query = query.where(Ticket.status == TicketStatus(status))
        except ValueError as exc: raise HTTPException(status_code=400, detail="工单状态无效") from exc
    if priority:
        try: query = query.where(Ticket.priority == TicketPriority(priority))
        except ValueError as exc: raise HTTPException(status_code=400, detail="工单优先级无效") from exc
    if category: query = query.where(Ticket.category == category)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (await db.execute(query.order_by(Ticket.created_at.desc()).offset((page-1)*page_size).limit(page_size))).scalars().all()
    result = {"total": total or 0, "page": page, "page_size": page_size, "tickets": [_ticket_dict(t) for t in rows]}

    return {
        "code": 0,
        "message": "获取成功",
        "data": result,
    }


@router.get("/tickets/stats")
async def get_ticket_stats(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    """获取工单统计"""
    rows = (await db.execute(select(Ticket.status, func.count()).group_by(Ticket.status))).all()
    status_counts = {status.value: count for status, count in rows}
    total = sum(status_counts.values())
    priority_rows = (await db.execute(select(Ticket.priority, func.count()).group_by(Ticket.priority))).all()
    priority_counts = {priority.value: count for priority, count in priority_rows}
    resolved = status_counts.get("resolved", 0) + status_counts.get("closed", 0)
    stats = {"total_tickets": total, "status_distribution": status_counts,
             "priority_distribution": priority_counts, "resolved_count": resolved,
             "resolution_rate": round(resolved / max(total, 1) * 100, 2)}

    return {
        "code": 0,
        "message": "获取成功",
        "data": stats,
    }


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """获取工单详情"""
    ticket = await db.get(Ticket, ticket_id)

    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")
    ensure_owner_or_admin(current_user, ticket.user_id)

    return {
        "code": 0,
        "message": "获取成功",
        "data": _ticket_dict(ticket),
    }


@router.put("/tickets/{ticket_id}")
async def update_ticket(
    ticket_id: str,
    request: TicketUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """更新工单"""
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")
    if request.status:
        try: new_status = TicketStatus(request.status)
        except ValueError as exc: raise HTTPException(status_code=400, detail="工单状态无效") from exc
        if new_status != ticket.status and new_status not in _ALLOWED_TRANSITIONS.get(ticket.status, set()):
            raise HTTPException(status_code=409, detail=f"不允许从 {ticket.status.value} 转换到 {new_status.value}")
        ticket.status = new_status
        if new_status in {TicketStatus.RESOLVED, TicketStatus.CLOSED}: ticket.resolved_at = datetime.utcnow()
    if request.priority:
        try: ticket.priority = TicketPriority(request.priority)
        except ValueError as exc: raise HTTPException(status_code=400, detail="工单优先级无效") from exc
    if request.assigned_to is not None: ticket.assigned_to = request.assigned_to
    if request.resolution is not None: ticket.resolution = request.resolution
    ticket.updated_at = datetime.utcnow()
    await db.commit()

    return {
        "code": 0,
        "message": "更新成功",
        "data": _ticket_dict(ticket),
    }


@router.delete("/tickets/{ticket_id}")
async def delete_ticket(ticket_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    """删除工单"""
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")
    await db.delete(ticket)
    await db.commit()

    return {
        "code": 0,
        "message": "删除成功",
    }


@router.post("/handoff")
async def request_handoff(request: HandoffRequest, db: AsyncSession = Depends(get_db), idempotency_header: Optional[str] = Header(default=None, alias="Idempotency-Key"), current_user: User = Depends(get_current_user)):
    """请求转人工"""
    ensure_owner_or_admin(current_user, request.user_id)
    idempotency_key = request.idempotency_key or idempotency_header
    if idempotency_key:
        existing = await db.scalar(select(HandoffRequestRecord).where(HandoffRequestRecord.idempotency_key == idempotency_key))
        if existing: return {"code": 0, "message": "幂等请求，返回已有记录", "data": _handoff_dict(existing)}
    session = await db.get(Session, request.session_id)
    if not session or session.user_id != request.user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    if request.priority not in {"urgent", "high", "normal", "low"}:
        raise HTTPException(status_code=400, detail="转人工优先级无效")
    now = datetime.utcnow(); hours = {"urgent": 1, "high": 4, "normal": 24, "low": 48}[request.priority]
    item = HandoffRequestRecord(id=str(uuid.uuid4()), user_id=request.user_id, session_id=request.session_id,
                                reason=request.reason, priority=request.priority, context=request.context or {},
                                status=HandoffStatus.PENDING, sla_deadline=now + timedelta(hours=hours),
                                idempotency_key=idempotency_key)
    db.add(item)
    db.add(NotificationRecord(id=str(uuid.uuid4()), event_type="handoff.created", recipient=str(request.user_id),
                              payload={"request_id": item.id}, idempotency_key=f"handoff-created:{item.id}"))
    db.add(OperationAudit(operation="request_handoff", idempotency_key=idempotency_key or f"handoff:{item.id}",
                          user_id=request.user_id, resource_id=item.id, request_payload=request.model_dump()))
    await db.commit()

    return {
        "code": 0,
        "message": "转人工请求已创建",
        "data": _handoff_dict(item),
    }


@router.get("/handoff/requests")
async def list_handoff_requests(
    status: Optional[str] = Query(default=None),
    priority: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """获取转人工请求列表"""
    query = select(HandoffRequestRecord)
    if status:
        try: query = query.where(HandoffRequestRecord.status == HandoffStatus(status))
        except ValueError as exc: raise HTTPException(status_code=400, detail="转人工状态无效") from exc
    if priority: query = query.where(HandoffRequestRecord.priority == priority)
    requests = (await db.execute(query.order_by(HandoffRequestRecord.created_at.desc()).limit(limit))).scalars().all()

    return {
        "code": 0,
        "message": "获取成功",
        "data": {
            "total": len(requests),
            "requests": [_handoff_dict(r) for r in requests],
        },
    }


@router.post("/handoff/{request_id}/assign")
async def assign_handoff(
    request_id: str,
    agent_id: str = Query(..., description="客服ID"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """分配人工客服"""
    item = await db.get(HandoffRequestRecord, request_id)
    if not item or item.status != HandoffStatus.PENDING:
        raise HTTPException(status_code=400, detail="分配失败，请检查客服ID或负载情况")
    item.assigned_to = agent_id; item.assigned_at = datetime.utcnow(); item.status = HandoffStatus.ASSIGNED
    await db.commit()

    return {
        "code": 0,
        "message": "分配成功",
        "data": {"request_id": request_id, "agent_id": agent_id},
    }


@router.post("/handoff/{request_id}/resolve")
async def resolve_handoff(
    request_id: str,
    resolution: str = Query(..., description="处理结果"),
    agent_id: Optional[str] = Query(default=None, description="客服ID"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """解决转人工请求"""
    item = await db.get(HandoffRequestRecord, request_id)
    if not item:
        raise HTTPException(status_code=404, detail="请求不存在")
    item.status = HandoffStatus.RESOLVED; item.resolution = resolution; item.resolved_at = datetime.utcnow()
    if agent_id: item.assigned_to = agent_id
    await db.commit()

    return {
        "code": 0,
        "message": "处理成功",
        "data": {"request_id": request_id, "resolution": resolution},
    }


@router.get("/agents")
async def list_agents(
    skill: Optional[str] = Query(default=None, description="技能筛选"),
    status: Optional[str] = Query(default=None, description="状态筛选"),
    _: User = Depends(require_admin),
):
    """获取人工客服列表"""
    collaboration_service = get_collaboration_service()
    agents = collaboration_service.human_agent_service.list_agents(
        skill=skill,
        status=status,
    )

    return {
        "code": 0,
        "message": "获取成功",
        "data": agents,
    }


@router.get("/collaboration/stats")
async def get_collaboration_stats(_: User = Depends(require_admin)):
    """获取人机协同统计"""
    collaboration_service = get_collaboration_service()
    stats = collaboration_service.get_collaboration_stats()

    return {
        "code": 0,
        "message": "获取成功",
        "data": stats,
    }
