"""
工单管理与人机协同 API - Phase 4 实现
"""
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends, Header
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.utils.database import get_db
from app.models.models import Ticket, TicketStatus, TicketPriority, User, UserRole, HandoffRequestRecord, HandoffStatus
from app.services.collaboration import get_collaboration_service
from app.services.ticket_creation import create_request, resolve_idempotency_key, ticket_receipt as _ticket_dict, handoff_receipt as _handoff_dict
from app.services.ticket_update import update_ticket as update_ticket_record
from app.services.handoff_assignment import assign_request, assigned_loads, effective_load
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


@router.post("/tickets")
async def create_ticket(
    request: TicketCreateRequest,
    db: AsyncSession = Depends(get_db),
    idempotency_header: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
):
    """创建工单"""
    ensure_owner_or_admin(current_user, request.user_id)
    idempotency_key = resolve_idempotency_key(request.idempotency_key, idempotency_header)
    try:
        TicketPriority(request.priority)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="工单优先级无效") from exc
    ticket, replayed = await create_request(
        db, current_user, "create_ticket", request.model_dump(exclude={"idempotency_key"}), idempotency_key,
    )

    return {
        "code": 0,
        "message": "幂等请求，返回已有工单" if replayed else "创建成功",
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
    idempotency_header: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """更新工单"""
    receipt = await update_ticket_record(db, current_user, ticket_id,
                                         request.model_dump(exclude_none=True), key=idempotency_header)

    return {
        "code": 0,
        "message": "更新成功",
        "data": receipt["ticket"],
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
    idempotency_key = resolve_idempotency_key(request.idempotency_key, idempotency_header)
    if request.priority not in {"urgent", "high", "normal", "low"}:
        raise HTTPException(status_code=400, detail="转人工优先级无效")
    payload = request.model_dump(exclude={"idempotency_key"})
    payload["context"] = request.context or {}
    item, replayed = await create_request(db, current_user, "request_handoff", payload, idempotency_key)

    return {
        "code": 0,
        "message": "幂等请求，返回已有记录" if replayed else "转人工请求已创建",
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


@router.get("/handoff/requests/{request_id}")
async def get_handoff_request(request_id: str, db: AsyncSession = Depends(get_db),
                              current_user: User = Depends(get_current_user)):
    item = await db.get(HandoffRequestRecord, request_id)
    if item is None:
        raise HTTPException(status_code=404, detail="转人工请求不存在")
    ensure_owner_or_admin(current_user, item.user_id)
    return {"code": 0, "message": "获取成功", "data": _handoff_dict(item)}


@router.post("/handoff/{request_id}/assign")
async def assign_handoff(
    request_id: str,
    agent_id: str = Query(..., description="客服ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """分配人工客服"""
    await assign_request(db, current_user, request_id, agent_id,
                         get_collaboration_service().human_agent_service)

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
    db: AsyncSession = Depends(get_db),
):
    """获取人工客服列表"""
    collaboration_service = get_collaboration_service()
    agents = collaboration_service.human_agent_service.list_agents(
        skill=skill,
        status=status,
    )
    loads = await assigned_loads(db)
    for data in agents:
        agent = collaboration_service.human_agent_service.get_agent(data["agent_id"])
        data["current_load"] = effective_load(agent, loads)

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
