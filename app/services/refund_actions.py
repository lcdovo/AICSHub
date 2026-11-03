"""M1退款预览/确认/审核领域服务；所有结果均为模拟，不调用支付。"""
import hashlib
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fastapi import HTTPException
from sqlalchemy import select
from app.models.models import (Order, RefundAction, RefundApplication, RefundBusinessClaim,
                               OperationAudit, Session, User)
from app.security import ensure_owner_or_admin
from app.config.config import settings
from app.agent.ticket_authorization import request_hash


class RefundActionCoordinator:
    """编排器的持久动作接口；会话/主体与资格仍由本领域服务核验。"""

    def __init__(self, session_factory: Callable[[], async_sessionmaker[AsyncSession]]):
        self._session_factory = session_factory

    async def prepare(self, actor_id: int, payload: dict, previous: str | None) -> dict:
        return await prepare(self._session_factory(), actor_id, payload, previous)

    async def pending_count(self, user_id: int, session_id: str) -> int:
        from sqlalchemy import func
        factory = self._session_factory()
        async with factory() as db:
            return int(await db.scalar(select(func.count()).select_from(RefundAction).where(
                RefundAction.user_id == user_id, RefundAction.session_id == session_id,
                RefundAction.status == "pending", RefundAction.expires_at > datetime.utcnow(),
            )) or 0)


def action_receipt(item: RefundAction) -> dict[str, Any]:
    return {"action_id": item.id, "version": item.version, "order_id": item.order_id,
            "type": item.type, "reason": item.reason, "amount": format(item.amount, ".2f"),
            "currency": item.currency, "status": item.status, "expires_at": item.expires_at.isoformat(),
            "application_id": item.application_id, "is_simulated": True}


async def lock_order(db: AsyncSession, owner_id: int, order_id: str) -> None:
    key = hashlib.sha256(f"refund-order:{owner_id}:{order_id}".encode()).hexdigest()
    dialect = db.get_bind().dialect.name
    if dialect == "mysql":
        from sqlalchemy.dialects.mysql import insert
        statement = insert(RefundBusinessClaim).values(id=key, revision=1)
        statement = statement.on_duplicate_key_update(revision=RefundBusinessClaim.revision + 1)
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
        statement = insert(RefundBusinessClaim).values(id=key, revision=1).on_conflict_do_update(
            index_elements=["id"], set_={"revision": RefundBusinessClaim.revision + 1})
    else:
        raise HTTPException(503, "数据库不支持安全退款协调")
    await db.execute(statement)


async def identity(db: AsyncSession, actor_id: int, owner_id: int, session_id: str | None = None) -> User:
    actor = await db.get(User, actor_id, populate_existing=True)
    if actor is None or not actor.status or actor.disabled_at is not None:
        raise HTTPException(403, "操作人已失效")
    ensure_owner_or_admin(actor, owner_id)
    if session_id is not None:
        session = await db.scalar(select(Session).where(Session.id == session_id).with_for_update()
                                  .execution_options(populate_existing=True))
        if session is None or session.user_id != owner_id or session.deleted_at is not None:
            raise HTTPException(404, "会话不存在")
        if session.status == "closed":
            raise HTTPException(409, "会话已关闭")
    return actor


async def qualification(db: AsyncSession, owner_id: int, order_id: str, *, exclude_application: str | None = None) -> tuple[Order, Decimal, Decimal, list[RefundApplication]]:
    order = await db.scalar(select(Order).where(Order.order_id == order_id, Order.user_id == owner_id).with_for_update())
    if order is None:
        raise HTTPException(404, "订单不存在或无权访问")
    if order.status_code not in {"shipped", "delivered", "completed"}:
        raise HTTPException(409, "当前订单状态不支持退换货")
    total = Decimal(str(order.total_amount))
    if not total.is_finite() or total <= 0 or order.currency != "CNY":
        raise HTTPException(409, "订单金额或币种不支持模拟全额申请")
    if order.received_at and (order.received_at > datetime.utcnow() or datetime.utcnow() > order.received_at + timedelta(days=7)):
        raise HTTPException(409, "订单签收时间无效或已超过7天期限")
    rows = (await db.execute(select(RefundApplication).where(
        RefundApplication.order_id == order_id, RefundApplication.user_id == owner_id,
        RefundApplication.status.in_(["pending_review", "approved"])).with_for_update())).scalars().all()
    reserved = sum((item.amount for item in rows if item.id != exclude_application), Decimal("0"))
    remaining = total - Decimal(str(order.refunded_amount or 0)) - reserved
    return order, total, remaining, rows


def business_digest(order_id: str, kind: str, reason: str, amount: Decimal, currency: str) -> str:
    return request_hash({"order_id": order_id, "type": kind, "reason": reason,
                         "amount": format(amount, ".2f"), "currency": currency})


async def prepare(factory: async_sessionmaker[AsyncSession], actor_id: int, payload: dict[str, Any], previous_id: str | None = None) -> dict[str, Any]:
    async with factory() as db:
        await identity(db, actor_id, payload["user_id"], payload["session_id"])
        await lock_order(db, payload["user_id"], payload["order_id"])
        order, total, remaining, applications = await qualification(db, payload["user_id"], payload["order_id"])
        existing = next((a for a in applications if a.type == payload["type"] and a.status == "pending_review"), None)
        digest = business_digest(order.order_id, payload["type"], payload["reason"], total, order.currency)
        if existing is not None and existing.business_hash != digest:
            raise HTTPException(409, "该订单已有不同参数的待审核申请")
        if existing is None and remaining < total:
            raise HTTPException(409, "订单剩余可申请额度不足；本版不支持部分退款")
        item = await db.scalar(select(RefundAction).where(RefundAction.id == previous_id).with_for_update()) if previous_id else None
        if item is not None:
            if item.user_id != payload["user_id"] or item.session_id != payload["session_id"]:
                raise HTTPException(403, "无权修改该动作")
            if item.status == "submitted":
                item = None
        if item is None:
            item = RefundAction(id=str(uuid.uuid4()), user_id=payload["user_id"], actor_id=actor_id,
                                session_id=payload["session_id"], version=1)
            db.add(item)
        else:
            item.version += 1
        item.order_id, item.type, item.reason = order.order_id, payload["type"], payload["reason"]
        item.amount, item.currency, item.status = total, order.currency, "pending"
        item.expires_at = datetime.utcnow() + timedelta(seconds=settings.REFUND_ACTION_TTL_SECONDS)
        db.add(OperationAudit(operation="prepare_refund", idempotency_key=f"refund-preview:{item.id}:{item.version}",
                              user_id=item.user_id, resource_id=item.id,
                              request_payload={"actor_id": actor_id, "owner_id": item.user_id},
                              result_payload={"action_id": item.id, "version": item.version}))
        await db.commit()
        return action_receipt(item)


async def cancel(factory: async_sessionmaker[AsyncSession], actor_id: int, owner_id: int, action_id: str) -> dict[str, Any]:
    async with factory() as db:
        await identity(db, actor_id, owner_id)
        item = await db.get(RefundAction, action_id)
        if item is None:
            raise HTTPException(404, "退款动作不存在")
        ensure_owner_or_admin(await db.get(User, actor_id), item.user_id)
        if item.user_id != owner_id:
            raise HTTPException(403, "动作不属于当前主体")
        await lock_order(db, owner_id, item.order_id)
        item = await db.scalar(select(RefundAction).where(RefundAction.id == action_id)
                               .with_for_update().execution_options(populate_existing=True))
        if item.status == "submitted":
            raise HTTPException(409, "已提交申请不能作为预览取消")
        if item.status != "cancelled":
            item.status = "cancelled"
            db.add(OperationAudit(operation="cancel_refund", idempotency_key=f"refund-cancel:{item.id}:{item.version}",
                                  user_id=item.user_id, resource_id=item.id,
                                  request_payload={"actor_id": actor_id, "owner_id": owner_id}))
            await db.commit()
        return action_receipt(item)


async def resolve_action(db: AsyncSession, payload: dict[str, Any], actor_id: int) -> RefundAction:
    await identity(db, actor_id, payload["user_id"], payload["session_id"])
    item = await db.scalar(select(RefundAction).where(RefundAction.id == payload.get("action_id"))
                           .with_for_update().execution_options(populate_existing=True))
    if item is None:
        raise HTTPException(409, "缺少持久退款预览，请重新预览")
    if item.user_id != payload["user_id"] or item.session_id != payload["session_id"]:
        raise HTTPException(403, "退款动作归属不匹配")
    if item.version != payload.get("action_version"):
        raise HTTPException(409, "退款预览版本已变化，请重新确认")
    if (item.order_id != payload["order_id"] or item.type != payload["type"] or item.reason != payload["reason"]):
        raise HTTPException(409, "提交参数与预览不一致，请重新预览")
    if item.status == "submitted":
        return item
    if item.status != "pending" or item.expires_at <= datetime.utcnow():
        raise HTTPException(409, "退款预览已取消或到期，请重新预览")
    return item


async def review(factory: async_sessionmaker[AsyncSession], actor_id: int, application_id: str, decision: str, note: str) -> dict[str, Any]:
    from app.services.refund_application import receipt
    async with factory() as db:
        actor = await db.get(User, actor_id)
        if actor is None or not actor.status or actor.disabled_at is not None or actor.role != "admin":
            raise HTTPException(403, "仅有效管理员可审核")
        item = await db.get(RefundApplication, application_id)
        if item is None:
            raise HTTPException(404, "申请不存在")
        await lock_order(db, item.user_id, item.order_id)
        item = await db.scalar(select(RefundApplication).where(RefundApplication.id == application_id)
                               .with_for_update().execution_options(populate_existing=True))
        if item.status != "pending_review":
            if item.status == decision and item.review_note == note:
                return receipt(item)
            raise HTTPException(409, "申请已审核，结果不能覆盖")
        if decision == "approved":
            order, total, remaining, _ = await qualification(db, item.user_id, item.order_id, exclude_application=item.id)
            if order.received_at is None:
                raise HTTPException(409, "缺少签收时间，不能批准申请")
            if remaining < item.amount or total != item.amount:
                raise HTTPException(409, "订单金额或剩余额度已变化，不能批准")
        item.status, item.review_note, item.reviewed_at = decision, note, datetime.utcnow()
        db.add(OperationAudit(operation="review_refund", idempotency_key=f"refund-review:{item.id}", user_id=item.user_id,
                              resource_id=item.id, request_payload={"actor_id": actor_id, "decision": decision, "note": note},
                              result_payload={"status": decision, "is_simulated": True, "paid": False}))
        await db.commit()
        return receipt(item)
