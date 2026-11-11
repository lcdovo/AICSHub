"""有界SQL任务与Outbox。未知投递只对账，不把至少一次称为恰好一次。"""
from __future__ import annotations
from app.utils.utc_time import utc_now_db

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from datetime import datetime, timedelta
import uuid

from sqlalchemy import select, update, or_
from sqlalchemy.exc import IntegrityError

from app.models.models import BackgroundJob, NotificationRecord, NotificationStatus, SimulatedDelivery, Session, Message


def enqueue(db: AsyncSession, kind: str, resource_id: str, owner_id: int | None, event_key: str) -> None:
    db.add(BackgroundJob(id=str(uuid.uuid4()), event_key=event_key, kind=kind, resource_id=resource_id, user_id=owner_id))


async def import_outbox(factory: async_sessionmaker[AsyncSession], limit: int = 50) -> int:
    """兼容历史已提交Outbox；只引用ID，不复制正文/地址。"""
    count = 0
    async with factory() as db:
        ids = (await db.execute(select(NotificationRecord.id).where(NotificationRecord.status.in_(
            [NotificationStatus.PENDING, NotificationStatus.SENDING, NotificationStatus.UNKNOWN])).limit(limit))).scalars().all()
    for nid in ids:
        async with factory() as db:
            if await db.scalar(select(BackgroundJob.id).where(BackgroundJob.event_key == "notification:" + nid)):
                continue
            item = await db.get(NotificationRecord, nid)
            enqueue(db, "notification", nid, int(item.recipient) if item.recipient.isdecimal() else None, "notification:" + nid)
            try:
                await db.commit()
                count += 1
            except IntegrityError:
                await db.rollback()
    return count


async def claim(factory: async_sessionmaker[AsyncSession], *, kind: str | None = None, lease_seconds: int = 30) -> tuple[str,str] | None:
    async with factory() as db:
        now = utc_now_db()
        eligible = or_(BackgroundJob.status.in_(["queued", "retrying"]),
                       (BackgroundJob.status == "running") & (BackgroundJob.lease_expires_at <= now))
        query = select(BackgroundJob).where(eligible, BackgroundJob.next_run <= now,
                                            BackgroundJob.attempts < BackgroundJob.max_attempts)
        if kind:
            query = query.where(BackgroundJob.kind == kind)
        candidates = (await db.execute(query.order_by(BackgroundJob.next_run, BackgroundJob.id).limit(10))).scalars().all()
        for item in candidates:
            token = str(uuid.uuid4())
            updated = await db.execute(update(BackgroundJob).where(BackgroundJob.id == item.id, eligible,
                BackgroundJob.attempts < BackgroundJob.max_attempts).values(status="running", lease_token=token,
                lease_expires_at=now+timedelta(seconds=lease_seconds), attempts=BackgroundJob.attempts+1))
            if updated.rowcount == 1:
                await db.commit()
                return item.id, token
        # 最后一次尝试崩溃也必须收敛，不能永久running。
        exhausted = (await db.execute(select(BackgroundJob.resource_id).where(BackgroundJob.kind == "notification",
            BackgroundJob.status == "running", BackgroundJob.lease_expires_at <= now,
            BackgroundJob.attempts >= BackgroundJob.max_attempts))).scalars().all()
        # 最后一次索引尝试进程消失时，版本也必须显示失败，不能永久indexing。
        from app.models.models import KnowledgeVersion
        failed_versions=(await db.execute(select(BackgroundJob.resource_id).where(BackgroundJob.kind=="knowledge_index",
            BackgroundJob.status=="running",BackgroundJob.lease_expires_at<=now,
            BackgroundJob.attempts>=BackgroundJob.max_attempts))).scalars().all()
        if failed_versions:
            await db.execute(update(KnowledgeVersion).where(KnowledgeVersion.id.in_(failed_versions),
                KnowledgeVersion.status.in_(["draft","indexing"]))
                .values(status="failed",error_code="attempts_exhausted"))
        if exhausted:
            await db.execute(update(NotificationRecord).where(NotificationRecord.id.in_(exhausted),
                NotificationRecord.status == NotificationStatus.SENDING)
                .values(status=NotificationStatus.UNKNOWN,last_error="attempts_exhausted"))
        await db.execute(update(BackgroundJob).where(BackgroundJob.status == "running",
            BackgroundJob.lease_expires_at <= now, BackgroundJob.attempts >= BackgroundJob.max_attempts)
            .values(status="failed", last_error="attempts_exhausted"))
        await db.commit()
    return None


async def _owned(db: AsyncSession, jid: str, token: str) -> BackgroundJob:
    result = await db.execute(update(BackgroundJob).where(BackgroundJob.id == jid, BackgroundJob.lease_token == token,
        BackgroundJob.status == "running", BackgroundJob.lease_expires_at > utc_now_db())
        .values(updated_at=utc_now_db()))
    if result.rowcount != 1:
        raise RuntimeError("job_lease_lost")
    return await db.get(BackgroundJob, jid, populate_existing=True)


class SimulatedAdapter:
    """模拟供应商支持event_id幂等/查询；不执行外部通知。"""
    is_simulated = True

    async def lookup(self, factory, event_id: str) -> str | None:
        async with factory() as db:
            item = await db.get(SimulatedDelivery, event_id)
            return item.receipt_id if item else None

    async def send(self, factory, event_id: str) -> str:
        async with factory() as db:
            item = SimulatedDelivery(event_id=event_id, receipt_id="simulated:"+event_id)
            db.add(item)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
            return "simulated:"+event_id


async def _summary(factory: async_sessionmaker[AsyncSession], jid: str, token: str) -> None:
    async with factory() as db:
        job = await _owned(db, jid, token)
        session = await db.scalar(select(Session).where(Session.id == job.resource_id).with_for_update())
        if session is not None and session.deleted_at is None:
            # 游标只推进已纳入本次摘要的消息；持续追加不会漏掉未覆盖尾部。
            rows = (await db.execute(select(Message).where(Message.session_id == session.id,
                Message.id > session.summary_cursor).order_by(Message.id).limit(40))).scalars().all()
            if len(rows) >= 20:
                old = rows[:-10]
                from app.services.llm_governance import SensitiveDataRedactor
                redactor = SensitiveDataRedactor()
                snippets = [f"{row.id}:{row.role.value}:{redactor.redact(row.content)[:70]}" for row in old]
                session.summary = ((session.summary or "") + "\n" + "\n".join(snippets))[-1200:]
                session.summary_cursor, session.summary_policy = old[-1].id, "rules-v1"
                session.summary_version += 1
        job.status, job.lease_token, job.lease_expires_at = "succeeded", None, None
        await db.commit()


async def perform(factory: async_sessionmaker[AsyncSession], jid: str, token: str, *, adapter=None, after_send=None) -> None:
    try:
        async with factory() as db:
            job = await _owned(db, jid, token)
            kind, resource_id = job.kind, job.resource_id
            await db.commit()
        if kind == "knowledge_index":
            import asyncio
            from app.services.knowledge_base import get_knowledge_base
            from app.services.knowledge_base.index_worker import index_job
            kb=await asyncio.to_thread(get_knowledge_base)
            await asyncio.to_thread(index_job,kb._repository,kb._embedding_service,kb._vector_store,jid,token)
            return
        if kind == "summary":
            await _summary(factory, jid, token)
            return
        if kind != "notification":
            raise ValueError("handler_not_registered")
        async with factory() as db:
            await _owned(db, jid, token)
            item = await db.get(NotificationRecord, resource_id)
            if item is None:
                raise ValueError("outbox_missing")
            already_sent = item.status == NotificationStatus.SENT and bool(item.provider_receipt)
            uncertain = item.status in (NotificationStatus.SENDING, NotificationStatus.UNKNOWN)
            if not already_sent:
                item.status, item.attempts = NotificationStatus.SENDING, item.attempts+1
            await db.commit()
        if already_sent:
            receipt = None
        elif adapter is None:
            raise ValueError("channel_unavailable")
        else:
            # 先查询；sending说明上次可能已经发出，没有对账证据绝不再次发送。
            receipt = await adapter.lookup(factory, resource_id)
            if receipt is None and uncertain:
                raise RuntimeError("delivery_unknown")
            if receipt is None:
                receipt = await adapter.send(factory, resource_id)
            if not isinstance(receipt,str) or not receipt:
                raise RuntimeError("delivery_unknown")
            if after_send:
                await after_send()
        async with factory() as db:
            job = await _owned(db, jid, token)
            item = await db.get(NotificationRecord, resource_id)
            if not already_sent:
                item.status, item.provider_receipt = NotificationStatus.SENT, receipt
                item.is_simulated, item.sent_at = bool(adapter.is_simulated), utc_now_db()
            job.status, job.lease_token, job.lease_expires_at = "succeeded", None, None
            await db.commit()
    except Exception as exc:
        # 只保存固定错误码，不落异常正文、任务全文、收件地址或供应商凭据。
        code = str(exc) if isinstance(exc, (ValueError,RuntimeError)) and str(exc) in {
            "handler_not_registered","outbox_missing","channel_unavailable","delivery_unknown","job_lease_lost"} else "dependency_error"
        async with factory() as db:
            try:
                job = await _owned(db, jid, token)
            except RuntimeError:
                return
            retryable = code == "dependency_error" and job.attempts < job.max_attempts
            job.status = "retrying" if retryable else "failed"
            job.next_run = utc_now_db()+timedelta(seconds=min(300,2**job.attempts))
            job.last_error, job.lease_token, job.lease_expires_at = code, None, None
            if job.kind == "notification":
                item = await db.get(NotificationRecord,job.resource_id)
                if item:
                    item.status = NotificationStatus.FAILED if code in ("channel_unavailable","outbox_missing") else NotificationStatus.UNKNOWN
                    item.last_error = code
            await db.commit()


async def tick(factory: async_sessionmaker[AsyncSession], *, kind=None, adapter=None) -> bool:
    owned = await claim(factory, kind=kind)
    if owned is None:
        return False
    await perform(factory,*owned,adapter=adapter)
    return True
