"""
工单管理与人机协同 API - Phase 4 实现
"""
import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.utils.database import get_db
from app.models.models import Ticket, TicketStatus, TicketPriority, User, UserRole
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


@router.post("/tickets")
async def create_ticket(
    request: TicketCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建工单"""
    ensure_owner_or_admin(current_user, request.user_id)
    collaboration_service = get_collaboration_service()

    ticket = collaboration_service.ticket_manager.create_ticket(
        user_id=request.user_id,
        category=request.category,
        content=request.content,
        priority=request.priority,
        session_id=request.session_id,
    )

    return {
        "code": 0,
        "message": "创建成功",
        "data": ticket,
    }


@router.get("/tickets")
async def list_tickets(
    status: Optional[str] = Query(default=None, description="工单状态"),
    priority: Optional[str] = Query(default=None, description="优先级"),
    category: Optional[str] = Query(default=None, description="分类"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """获取工单列表"""
    collaboration_service = get_collaboration_service()

    if current_user.role != UserRole.ADMIN.value:
        # 当前内存 TicketManager 只支持过滤条件，使用 user_id 过滤避免跨用户泄露。
        user_filter = current_user.id
    else:
        user_filter = None

    result = collaboration_service.ticket_manager.list_tickets(
        status=status,
        priority=priority,
        category=category,
        page=page,
        page_size=page_size,
        user_id=user_filter,
    )

    return {
        "code": 0,
        "message": "获取成功",
        "data": result,
    }


@router.get("/tickets/stats")
async def get_ticket_stats(_: User = Depends(require_admin)):
    """获取工单统计"""
    collaboration_service = get_collaboration_service()
    stats = collaboration_service.ticket_manager.get_ticket_stats()

    return {
        "code": 0,
        "message": "获取成功",
        "data": stats,
    }


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, current_user: User = Depends(get_current_user)):
    """获取工单详情"""
    collaboration_service = get_collaboration_service()
    ticket = collaboration_service.ticket_manager.get_ticket(ticket_id)

    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")
    ensure_owner_or_admin(current_user, ticket.get("user_id"))

    return {
        "code": 0,
        "message": "获取成功",
        "data": ticket,
    }


@router.put("/tickets/{ticket_id}")
async def update_ticket(
    ticket_id: str,
    request: TicketUpdateRequest,
    current_user: User = Depends(require_admin),
):
    """更新工单"""
    collaboration_service = get_collaboration_service()

    ticket = collaboration_service.ticket_manager.update_ticket_status(
        ticket_id=ticket_id,
        status=request.status or "",
        assigned_to=request.assigned_to,
        resolution=request.resolution,
    )

    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")

    return {
        "code": 0,
        "message": "更新成功",
        "data": ticket,
    }


@router.delete("/tickets/{ticket_id}")
async def delete_ticket(ticket_id: str, current_user: User = Depends(require_admin)):
    """删除工单"""
    collaboration_service = get_collaboration_service()

    success = collaboration_service.ticket_manager.delete_ticket(ticket_id)
    if not success:
        raise HTTPException(status_code=404, detail="工单不存在")

    return {
        "code": 0,
        "message": "删除成功",
    }


@router.post("/handoff")
async def request_handoff(request: HandoffRequest, current_user: User = Depends(get_current_user)):
    """请求转人工"""
    ensure_owner_or_admin(current_user, request.user_id)
    collaboration_service = get_collaboration_service()

    result = collaboration_service.execute_handoff(
        user_id=request.user_id,
        session_id=request.session_id,
        reason=request.reason,
        priority=request.priority,
        context=request.context,
    )

    return {
        "code": 0,
        "message": "转人工请求已创建",
        "data": result,
    }


@router.get("/handoff/requests")
async def list_handoff_requests(
    status: Optional[str] = Query(default=None),
    priority: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    _: User = Depends(require_admin),
):
    """获取转人工请求列表"""
    collaboration_service = get_collaboration_service()
    requests = collaboration_service.human_agent_service.list_handoff_requests(
        status=status,
        priority=priority,
        limit=limit,
    )

    return {
        "code": 0,
        "message": "获取成功",
        "data": {
            "total": len(requests),
            "requests": requests,
        },
    }


@router.post("/handoff/{request_id}/assign")
async def assign_handoff(
    request_id: str,
    agent_id: str = Query(..., description="客服ID"),
    _: User = Depends(require_admin),
):
    """分配人工客服"""
    collaboration_service = get_collaboration_service()
    success = collaboration_service.human_agent_service.assign_handoff_request(
        request_id=request_id,
        agent_id=agent_id,
    )

    if not success:
        raise HTTPException(status_code=400, detail="分配失败，请检查客服ID或负载情况")

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
    _: User = Depends(require_admin),
):
    """解决转人工请求"""
    collaboration_service = get_collaboration_service()
    success = collaboration_service.human_agent_service.resolve_handoff_request(
        request_id=request_id,
        resolution=resolution,
        agent_id=agent_id,
    )

    if not success:
        raise HTTPException(status_code=404, detail="请求不存在")

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
