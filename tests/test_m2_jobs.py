from datetime import datetime,timedelta
import pytest
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError

from app.models.models import BackgroundJob,NotificationRecord,NotificationStatus,SimulatedDelivery,Message,MessageRole,Session
from app.services import background_jobs as jobs
from tests.test_ticket_idempotency import api_context,send,payload


async def seed_event(factory,event_id="synthetic-event",key="synthetic-key"):
    async with factory() as db:
        db.add(NotificationRecord(id=event_id,event_type="synthetic.created",recipient="101",payload={"resource_id":"synthetic"},idempotency_key=key))
        jobs.enqueue(db,"notification",event_id,101,"notification:"+event_id)
        await db.commit()


async def test_business_outbox_and_job_share_commit_and_event_dedup(tmp_path):
    async with api_context(tmp_path/"jobs.db") as (client,factory):
        for _ in range(3): assert (await send(client,"tickets",payload("tickets"))).status_code==200
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(NotificationRecord))==1
            assert await db.scalar(select(func.count()).select_from(BackgroundJob))==1
            jobs.enqueue(db,"notification","rolled-back",101,"rollback-key")
            await db.rollback()
        assert await jobs.import_outbox(factory)==0
        await seed_event(factory,"other-event","other-key")
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(BackgroundJob))==2


@pytest.mark.parametrize("mode",["missing","simulated","provider_substitute"])
async def test_notification_status_requires_receipt_and_truthful_adapter(tmp_path,mode):
    async with api_context(tmp_path/"channel.db") as (_,factory):
        await seed_event(factory)
        adapter=jobs.SimulatedAdapter() if mode!="missing" else None
        if mode=="provider_substitute": adapter.is_simulated=False
        assert await jobs.tick(factory,adapter=adapter)
        async with factory() as db:
            event=await db.get(NotificationRecord,"synthetic-event")
            job=await db.scalar(select(BackgroundJob))
            if mode=="missing":
                assert event.status==NotificationStatus.FAILED and not event.provider_receipt
                assert job.status=="failed" and job.last_error=="channel_unavailable"
            else:
                assert event.status==NotificationStatus.SENT and event.provider_receipt
                assert event.is_simulated==(mode=="simulated") and job.status=="succeeded"


async def test_send_committed_ack_lost_reconciles_without_second_delivery(tmp_path):
    async with api_context(tmp_path/"ack.db") as (_,factory):
        await seed_event(factory)
        owned=await jobs.claim(factory)
        async def lost(): raise TimeoutError("synthetic-private-token-must-not-be-logged")
        await jobs.perform(factory,*owned,adapter=jobs.SimulatedAdapter(),after_send=lost)
        async with factory() as db:
            job=await db.get(BackgroundJob,owned[0])
            assert job.status=="retrying" and job.last_error=="dependency_error"
            job.next_run=datetime.utcnow()-timedelta(seconds=1)
            assert (await db.get(NotificationRecord,"synthetic-event")).status==NotificationStatus.UNKNOWN
            await db.commit()
        assert await jobs.tick(factory,adapter=jobs.SimulatedAdapter())
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(SimulatedDelivery))==1
            assert (await db.get(NotificationRecord,"synthetic-event")).status==NotificationStatus.SENT


async def test_unknown_delivery_without_lookup_proof_never_resends(tmp_path):
    class UnknownAdapter:
        is_simulated=False
        async def lookup(self,*args): return None
        async def send(self,*args): pytest.fail("unknown delivery must not be resent")
    async with api_context(tmp_path/"unknown.db") as (_,factory):
        await seed_event(factory)
        async with factory() as db:
            (await db.get(NotificationRecord,"synthetic-event")).status=NotificationStatus.SENDING
            await db.commit()
        await jobs.tick(factory,adapter=UnknownAdapter())
        async with factory() as db:
            assert (await db.get(NotificationRecord,"synthetic-event")).status==NotificationStatus.UNKNOWN
            assert (await db.scalar(select(BackgroundJob))).last_error=="delivery_unknown"


async def test_expired_job_fencing_old_owner_cannot_ack(tmp_path):
    async with api_context(tmp_path/"lease.db") as (_,factory):
        await seed_event(factory)
        old=await jobs.claim(factory)
        async with factory() as db:
            (await db.get(BackgroundJob,old[0])).lease_expires_at=datetime.utcnow()-timedelta(seconds=1)
            await db.commit()
        new=await jobs.claim(factory)
        assert new[0]==old[0] and new[1]!=old[1]
        await jobs.perform(factory,*old,adapter=jobs.SimulatedAdapter())
        async with factory() as db:
            assert (await db.get(BackgroundJob,new[0])).lease_token==new[1]
        await jobs.perform(factory,*new,adapter=jobs.SimulatedAdapter())
        async with factory() as db:
            assert (await db.get(BackgroundJob,new[0])).status=="succeeded"


async def test_summary_cursor_is_monotonic_and_resumes_without_recount(tmp_path):
    async with api_context(tmp_path/"summary.db") as (_,factory):
        async with factory() as db:
            for index in range(50): db.add(Message(session_id="session-101",role=MessageRole.USER,content=f"合成历史{index}"))
            jobs.enqueue(db,"summary","session-101",101,"summary-1")
            await db.commit()
        await jobs.tick(factory,kind="summary")
        async with factory() as db:
            first=await db.get(Session,"session-101")
            assert first.summary_cursor==30 and first.summary_version==1 and first.summary_policy=="rules-v1"
            jobs.enqueue(db,"summary","session-101",101,"summary-2")
            await db.commit()
        await jobs.tick(factory,kind="summary")
        async with factory() as db:
            second=await db.get(Session,"session-101")
            assert second.summary_cursor==40 and second.summary_version==2
            assert "41:" not in second.summary


async def test_unknown_handler_max_attempt_and_payload_never_saved(tmp_path):
    async with api_context(tmp_path/"fail.db") as (_,factory):
        async with factory() as db:
            jobs.enqueue(db,"unregistered","resource-only",101,"unknown-handler")
            await db.commit()
        await jobs.tick(factory)
        async with factory() as db:
            job=await db.scalar(select(BackgroundJob))
            assert job.status=="failed" and job.last_error=="handler_not_registered"
            assert not hasattr(job,"payload")
            job.status="running";job.attempts=job.max_attempts
            job.lease_expires_at=datetime.utcnow()-timedelta(seconds=1)
            await db.commit()
        assert await jobs.claim(factory) is None
        async with factory() as db:
            assert (await db.scalar(select(BackgroundJob))).status=="failed"


async def test_job_retry_and_notification_owner_permissions(tmp_path):
    from fastapi import FastAPI
    import httpx
    from app.routers.jobs import router
    from app.utils.database import get_db
    from app.security import create_access_token
    from app.models.models import User
    async with api_context(tmp_path/"permissions.db") as (_,factory):
        await seed_event(factory)
        await jobs.tick(factory)
        async with factory() as db:
            job=await db.scalar(select(BackgroundJob));jid=job.id
            tokens={uid:create_access_token(await db.get(User,uid)) for uid in (101,202,999)}
        app=FastAPI();app.include_router(router)
        async def override():
            async with factory() as db: yield db
        app.dependency_overrides[get_db]=override
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://synthetic") as client:
            for uid in (101,202):
                auth={"Authorization":"Bearer "+tokens[uid]}
                assert (await client.get("/api/v1/jobs/",headers=auth)).status_code==403
                assert (await client.post(f"/api/v1/jobs/{jid}/retry",headers=auth)).status_code==403
            own=await client.get("/api/v1/jobs/notifications/synthetic-event",headers={"Authorization":"Bearer "+tokens[101]})
            assert own.status_code==200 and own.json()["data"]["status"]=="failed"
            assert (await client.get("/api/v1/jobs/notifications/synthetic-event",headers={"Authorization":"Bearer "+tokens[202]})).status_code==403
            retried=await client.post(f"/api/v1/jobs/{jid}/retry",headers={"Authorization":"Bearer "+tokens[999]})
            assert retried.status_code==200 and retried.json()["data"]["status"]=="queued"


async def test_repeated_dependency_failure_backoff_and_last_crash_converge(tmp_path):
    class FailingAdapter:
        is_simulated=False
        async def lookup(self,*args): raise TimeoutError("synthetic-secret-not-stored")
        async def send(self,*args): pytest.fail("unproven delivery must not send")
    async with api_context(tmp_path/"exhaustion.db") as (_,factory):
        await seed_event(factory)
        for attempt in range(1,4):
            before=datetime.utcnow()
            assert await jobs.tick(factory,adapter=FailingAdapter())
            async with factory() as db:
                job=await db.scalar(select(BackgroundJob))
                assert job.attempts==attempt and job.last_error=="dependency_error"
                assert job.next_run>=before+timedelta(seconds=2**attempt)
                assert job.status==("retrying" if attempt<3 else "failed")
                job.next_run=datetime.utcnow()-timedelta(seconds=1)
                await db.commit()
        assert not await jobs.tick(factory,adapter=FailingAdapter())
        async with factory() as db:
            job=await db.scalar(select(BackgroundJob));job.status="running"
            job.lease_expires_at=datetime.utcnow()-timedelta(seconds=1)
            (await db.get(NotificationRecord,"synthetic-event")).status=NotificationStatus.SENDING
            await db.commit()
        assert await jobs.claim(factory) is None
        async with factory() as db:
            assert (await db.scalar(select(BackgroundJob))).last_error=="attempts_exhausted"
            assert (await db.get(NotificationRecord,"synthetic-event")).status==NotificationStatus.UNKNOWN


async def test_same_event_dedup_preserves_distinct_events_for_same_resource(tmp_path):
    async with api_context(tmp_path/"events.db") as (_,factory):
        async with factory() as db:
            jobs.enqueue(db,"summary","session-101",101,"event-one");await db.commit()
            jobs.enqueue(db,"summary","session-101",101,"event-one")
            with pytest.raises(IntegrityError): await db.commit()
            await db.rollback()
            jobs.enqueue(db,"summary","session-101",101,"event-two");await db.commit()
            assert await db.scalar(select(func.count()).select_from(BackgroundJob))==2


async def test_business_rollback_has_no_consumable_job(tmp_path,monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.models import Ticket,OperationAudit
    async with api_context(tmp_path/"business-rollback.db") as (client,factory):
        original=AsyncSession.commit
        async def fail(db):
            await db.flush()
            raise RuntimeError("synthetic business commit failure")
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession,"commit",fail)
            with pytest.raises(RuntimeError): await send(client,"tickets",payload("tickets"))
        async with factory() as db:
            for model in (Ticket,NotificationRecord,BackgroundJob,OperationAudit):
                assert await db.scalar(select(func.count()).select_from(model))==0
        assert not await jobs.tick(factory,adapter=jobs.SimulatedAdapter())
        assert AsyncSession.commit is original
