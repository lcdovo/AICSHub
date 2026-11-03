"""模拟退换货申请结果查询；不提供支付或自动审核。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from app.models.models import RefundAction, RefundApplication, Session, User
from app.security import ensure_owner_or_admin, get_current_user
from app.services.refund_application import receipt
from app.utils.database import get_db
from app.services import refund_actions

router = APIRouter(prefix="/api/v1/refunds", tags=["模拟退换货申请"])


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    user_id: int = Field(gt=0)
    session_id: str
    order_id: str
    type: Literal["refund", "return", "exchange"] = "refund"
    reason: str = Field(min_length=3, max_length=10000)
    previous_action_id: str | None = None


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["approved", "rejected"]
    note: str = Field(min_length=1, max_length=1000)


@router.post("/actions")
async def preview_refund(body: PreviewRequest, current_user: User = Depends(get_current_user)):
    from app.utils.database import async_session
    payload = body.model_dump(exclude={"previous_action_id"})
    payload["order_id"], payload["reason"] = payload["order_id"].strip().upper(), payload["reason"].strip()
    action = await refund_actions.prepare(async_session, current_user.id, payload, body.previous_action_id)
    return {"code": 0, "message": "仅预览，尚未提交申请", "data": action}


@router.get("/actions/pending")
async def pending_actions(session_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    ensure_owner_or_admin(current_user, session.user_id)
    actions = (await db.execute(select(RefundAction).where(RefundAction.session_id == session_id,
                               RefundAction.status == "pending"))).scalars().all()
    return {"code": 0, "data": [refund_actions.action_receipt(a) for a in actions]}


@router.post("/actions/{action_id}/cancel")
async def cancel_action(action_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    from app.utils.database import async_session
    item = await db.get(RefundAction, action_id)
    if item is None:
        raise HTTPException(404, "动作不存在")
    ensure_owner_or_admin(current_user, item.user_id)
    result = await refund_actions.cancel(async_session, current_user.id, item.user_id, action_id)
    return {"code": 0, "data": result}


@router.post("/{refund_id}/review")
async def review_refund(refund_id: str, body: ReviewRequest, current_user: User = Depends(get_current_user)):
    from app.utils.database import async_session
    result = await refund_actions.review(async_session, current_user.id, refund_id, body.decision, body.note)
    return {"code": 0, "message": "模拟审核已记录，未执行支付", "data": result}


@router.get("/{refund_id}")
async def get_refund(refund_id: str, db: AsyncSession = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    item = await db.get(RefundApplication, refund_id)
    if item is None:
        raise HTTPException(status_code=404, detail="申请不存在")
    ensure_owner_or_admin(current_user, item.user_id)
    return {"code": 0, "message": "获取成功", "data": receipt(item)}


@router.get("/")
async def list_refunds(session_id: str | None = None, db: AsyncSession = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    from sqlalchemy import or_
    query = select(RefundApplication)
    if current_user.role != "admin":
        query = query.where(RefundApplication.user_id == current_user.id)
    if session_id:
        session = await db.get(Session, session_id)
        if session is None:
            raise HTTPException(404, "会话不存在")
        ensure_owner_or_admin(current_user, session.user_id)
        linked = select(RefundAction.application_id).where(RefundAction.session_id == session_id)
        query = query.where(or_(RefundApplication.session_id == session_id, RefundApplication.id.in_(linked)))
    items = (await db.execute(query.order_by(RefundApplication.created_at.desc()).limit(50))).scalars().all()
    return {"code": 0, "data": [receipt(item) for item in items]}
