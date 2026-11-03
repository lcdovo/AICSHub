"""模拟退换货申请事务；无支付/发送渠道和自动审核。"""
import hashlib
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.refund_authorization import CONFIRMATIONS
from app.agent.ticket_authorization import request_hash
from app.models.models import Message, MessageRole, OperationAudit, Order, RefundApplication, Session, User
from app.security import ensure_owner_or_admin


def receipt(item: RefundApplication) -> dict[str, Any]:
    return {"refund_id": item.id, "order_id": item.order_id, "type": item.type,
            "type_label": {"refund": "仅退款", "return": "退货退款", "exchange": "换货"}[item.type],
            "reason": item.reason, "amount": format(item.amount, ".2f"), "currency": item.currency,
            "status": item.status, "status_label": "模拟申请待审核", "is_simulated": item.is_simulated,
            "apply_time": item.created_at.isoformat(),
            "next_steps": ["此为模拟申请登记，尚未审核或执行退款/换货。"]}


async def context(db: AsyncSession, payload: dict[str, Any], message_id: int, actor_id: int) -> User:
    actor = await db.get(User, actor_id)
    source = await db.get(Message, message_id)
    session = await db.get(Session, payload["session_id"])
    if (actor is None or not actor.status or actor.disabled_at is not None
            or source is None or source.role != MessageRole.USER or source.content.strip() not in CONFIRMATIONS
            or source.session_id != payload["session_id"] or session is None or session.user_id != payload["user_id"]):
        raise HTTPException(status_code=403, detail="退款提交缺少本轮明确确认或有效身份")
    ensure_owner_or_admin(actor, payload["user_id"])
    return actor


async def order_amount(db: AsyncSession, payload: dict[str, Any]) -> Decimal:
    order = await db.scalar(select(Order).where(
        Order.order_id == payload["order_id"], Order.user_id == payload["user_id"]
    ).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在或无权访问")
    if order.status_code not in {"shipped", "delivered", "completed"}:
        raise HTTPException(status_code=409, detail="当前订单状态不支持退换货")
    try:
        amount = Decimal(str(order.total_amount))
        if not amount.is_finite() or amount <= 0 or amount >= Decimal("10000000000") or amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Invalid amount")
        return amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=409, detail="订单金额无法安全用于模拟申请，请核查订单") from exc


async def submit(factory: async_sessionmaker[AsyncSession], payload: dict[str, Any], message_id: int, actor_id: int) -> dict[str, Any]:
    key = "refund-v1:" + hashlib.sha256(f"{actor_id}:{payload['user_id']}:{payload['idempotency_key']}".encode()).hexdigest()
    digest = request_hash(payload)

    def replay(item: RefundApplication) -> dict[str, Any]:
        if item.request_hash != digest or item.user_id != payload["user_id"] or item.actor_id != actor_id:
            raise HTTPException(status_code=409, detail="幂等键已用于不同退款请求")
        return receipt(item)

    # 先完成只读校验及退出，再构造业务ID；读依赖失败不能生成任何申请回执。
    async with factory() as db:
        await context(db, payload, message_id, actor_id)
        amount = await order_amount(db, payload)
    async with factory() as db:
        await context(db, payload, message_id, actor_id)
        existing = await db.scalar(select(RefundApplication).where(RefundApplication.idempotency_key == key))
        if existing is not None:
            return replay(existing)
        amount = await order_amount(db, payload)  # 提交事务重新检查，不能沿用预检旧资格/金额。
        item = RefundApplication(id=str(uuid.uuid4()), user_id=payload["user_id"], actor_id=actor_id,
                                 session_id=payload["session_id"], source_message_id=message_id,
                                 order_id=payload["order_id"], type=payload["type"], reason=payload["reason"],
                                 amount=amount, idempotency_key=key, request_hash=digest)
        try:
            db.add(item)
            await db.flush()
            result = receipt(item)
            db.add(OperationAudit(operation="apply_refund", idempotency_key=key, user_id=item.user_id,
                                  resource_id=item.id,
                                  request_payload={"actor_id": actor_id, "owner_id": item.user_id,
                                                   "message_id": message_id, "request": payload, "request_hash": digest},
                                  result_payload=result))
            await db.commit()
            return result
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(select(RefundApplication).where(RefundApplication.idempotency_key == key))
            if existing is not None:
                return replay(existing)
            raise HTTPException(status_code=409, detail="退款申请冲突，请核查原请求") from None
