"""第 3 步持久化仓储：集中封装幂等查询和状态变更。"""
from app.utils.utc_time import utc_now_db
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    HandoffRequestRecord, HandoffStatus, NotificationRecord, NotificationStatus,
    Ticket, TicketStatus,
)


class TicketRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def by_idempotency_key(self, key: str) -> Optional[Ticket]:
        return await self.db.scalar(select(Ticket).where(Ticket.idempotency_key == key))

    async def get(self, ticket_id: str) -> Optional[Ticket]:
        return await self.db.get(Ticket, ticket_id)

    async def transition(self, ticket: Ticket, target: TicketStatus, allowed: dict[TicketStatus, set[TicketStatus]]) -> Ticket:
        if target != ticket.status and target not in allowed.get(ticket.status, set()):
            raise ValueError(f"不允许从 {ticket.status.value} 转换到 {target.value}")
        ticket.status = target
        ticket.updated_at = utc_now_db()
        if target in {TicketStatus.RESOLVED, TicketStatus.CLOSED}:
            ticket.resolved_at = utc_now_db()
        return ticket


class HandoffRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def by_idempotency_key(self, key: str) -> Optional[HandoffRequestRecord]:
        return await self.db.scalar(select(HandoffRequestRecord).where(HandoffRequestRecord.idempotency_key == key))

    async def get(self, request_id: str) -> Optional[HandoffRequestRecord]:
        return await self.db.get(HandoffRequestRecord, request_id)

    async def assign(self, item: HandoffRequestRecord, agent_id: str) -> HandoffRequestRecord:
        if item.status != HandoffStatus.PENDING:
            raise ValueError("仅待处理请求可分配")
        item.status = HandoffStatus.ASSIGNED
        item.assigned_to = agent_id
        item.assigned_at = utc_now_db()
        return item


class OutboxRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def enqueue(self, event_type: str, recipient: str, payload: dict, idempotency_key: str) -> NotificationRecord:
        existing = await self.db.scalar(select(NotificationRecord).where(NotificationRecord.idempotency_key == idempotency_key))
        if existing:
            return existing
        item = NotificationRecord(id=__import__("uuid").uuid4().__str__(), event_type=event_type,
                                  recipient=recipient, payload=payload, idempotency_key=idempotency_key)
        self.db.add(item)
        return item

    async def mark_sent(self, item: NotificationRecord) -> NotificationRecord:
        item.status = NotificationStatus.SENT
        item.attempts += 1
        item.sent_at = utc_now_db()
        item.last_error = None
        return item

    async def mark_failed(self, item: NotificationRecord, error: str) -> NotificationRecord:
        item.status = NotificationStatus.FAILED
        item.attempts += 1
        item.last_error = error[:2000]
        return item
