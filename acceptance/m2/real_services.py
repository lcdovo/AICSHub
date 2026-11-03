"""M2真实MySQL/Redis/实际进程；仅由专用标签校验入口运行。"""
import asyncio
from datetime import datetime,timedelta
import multiprocessing
import os,json,uuid
from pathlib import Path
import pytest
from sqlalchemy import create_engine,text,select,func
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from sqlalchemy.pool import NullPool
from app.config.config import settings
from app.models.models import Base,User,Session,Message,ChatRun,RunEvent,Ticket,BackgroundJob,NotificationRecord,NotificationStatus,SimulatedDelivery

@pytest.fixture
async def real_case():
    if not os.environ.get("M2_TEST_STATE"):
        raise RuntimeError("Must run isolated M2 entry")
    name="aicshub_m2_case_"+uuid.uuid4().hex[:12]
    base=settings.DATABASE_URL
    sync=create_engine(base.replace("+aiomysql","+pymysql"))
    with sync.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4"))
    sync.dispose()
    url=base.rsplit("/",1)[0]+"/"+name
    engine=create_async_engine(url,pool_pre_ping=True,poolclass=NullPool)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add_all([User(id=101,username="synthetic-a",role="user"),User(id=202,username="synthetic-b",role="user"),User(id=999,username="synthetic-admin",role="admin")])
        await db.flush()
        db.add(Session(id="case-session",user_id=101))
        await db.commit()
    yield url,factory
    await engine.dispose()


def make_service(factory, manager, kill=None):
    from types import SimpleNamespace
    from app.services.chat_service import ChatService
    import app.services.chat_service as shared
    shared.session_manager=manager
    import app.utils.database as database
    database.async_session=factory
    async def process(state):
        if state.user_message.startswith("合成技术问题"):
            iteration=int(state.user_message.removeprefix("合成技术问题"))
            if iteration:
                assert any(m["role"]=="assistant" and "工单" in m["content"] for m in state.messages)
                assert any(m["content"]==f"合成技术问题{iteration-1}" for m in state.messages)
        if kill=="after_user": os._exit(23)
        from app.agent.tools import CreateTicketTool
        from app.agent.ticket_authorization import authorize_ticket
        payload=dict(user_id=state.user_id,session_id=state.session_id,category="技术支持",content=state.user_message,priority="medium",
                     idempotency_key=f"chat-ticket:{state.session_id}:{state.source_message_id}")
        result=await CreateTicketTool().execute(**payload,authorization=authorize_ticket(payload,state.source_message_id,state.actor_id))
        if not result["success"]: raise RuntimeError("synthetic write failed")
        if kill=="after_tool": os._exit(23)
        state.reply="工单 "+result["data"]["ticket_id"]+" 已登记"
        return state
    return ChatService(SimpleNamespace(run=process))


def duplicate_worker(url,barrier,queue,redis_enabled):
    async def run():
        from app.agent.memory import SessionManager
        from app.schemas.schemas import ChatRequest
        from app.services import run_execution as runs
        from app.utils.database import close_redis
        engine=create_async_engine(url,poolclass=NullPool);factory=async_sessionmaker(engine,expire_on_commit=False)
        manager=SessionManager()
        if not redis_enabled: manager._redis_available=False
        service=make_service(factory,manager)
        try:
            for iteration in range(20):
                await asyncio.to_thread(barrier.wait,30)
                async with factory() as db:
                    current,_=await runs.accept(db,await db.get(User,101),ChatRequest(user_id=101,session_id="case-session",
                        message=f"合成技术问题{iteration}",client_message_id=f"same-{iteration}"))
                if current.status=="accepted": await runs.execute(factory,current.id,service,manager,None,False)
                result=await runs.wait(factory,current.id)
                queue.put(result["run_id"])
        finally:
            await close_redis();await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("redis_enabled",[False,True])
async def test_two_actual_workers_same_client_id_20_rounds(real_case,redis_enabled):
    url,factory=real_case
    ctx=multiprocessing.get_context("spawn");barrier=ctx.Barrier(2);queue=ctx.Queue()
    workers=[ctx.Process(target=duplicate_worker,args=(url,barrier,queue,redis_enabled)) for _ in range(2)]
    for worker in workers: worker.start()
    ids=await asyncio.to_thread(lambda:[queue.get(timeout=90) for _ in range(40)])
    for worker in workers:
        await asyncio.to_thread(worker.join,10)
        assert worker.exitcode==0
    assert len(set(ids))==20
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ChatRun))==20
        assert await db.scalar(select(func.count()).select_from(Ticket))==20
        assert await db.scalar(select(func.count()).select_from(Message))==40
        assert (await db.get(Session,"case-session")).version==20


def crash_worker(url,rid,point):
    async def run():
        from app.agent.memory import SessionManager
        from app.services import run_execution as runs
        engine=create_async_engine(url,poolclass=NullPool);factory=async_sessionmaker(engine,expire_on_commit=False)
        manager=SessionManager();manager._redis_available=False
        service=make_service(factory,manager,kill=point)
        if point=="before_final":
            def stop(*args): os._exit(23)
            runs.finish=stop
        if point=="after_final":
            async def stop(*args): os._exit(23)
            manager.save_state=stop
        await runs.execute(factory,rid,service,manager,None,False)
        await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("point",["after_user","after_tool","before_final","after_final"])
async def test_actual_process_crash_and_repeated_recovery(real_case,point):
    from app.schemas.schemas import ChatRequest
    from app.services import run_execution as runs
    url,factory=real_case
    async with factory() as db:
        original,_=await runs.accept(db,await db.get(User,101),ChatRequest(user_id=101,session_id="case-session",message="合成崩溃窗口问题",client_message_id="crash-case"))
    ctx=multiprocessing.get_context("spawn")
    worker=ctx.Process(target=crash_worker,args=(url,original.id,point));worker.start()
    await asyncio.to_thread(worker.join,60)
    assert worker.exitcode==23
    async with factory() as db:
        run=await db.get(ChatRun,original.id)
        already=run.result
        if point!="after_final":
            assert run.status=="running"
            assert not await db.scalar(select(RunEvent.id).where(RunEvent.run_id==run.id,RunEvent.event_type=="stream_end"))
            run.lease_expires_at=datetime.utcnow()-timedelta(seconds=1)
            (await db.get(Session,run.session_id)).lease_expires_at=datetime.utcnow()-timedelta(seconds=1)
            await db.commit()
    for _ in range(3): await runs.recover(factory,original.id)
    async with factory() as db:
        final=await db.get(ChatRun,original.id)
        if point=="after_user":
            assert final.status=="failed" and await db.scalar(select(func.count()).select_from(Ticket))==0
            assert await db.scalar(select(func.count()).select_from(Message))==1
        else:
            assert final.status=="succeeded" and await db.scalar(select(func.count()).select_from(Ticket))==1
            assert await db.scalar(select(func.count()).select_from(Message))==2
            if already: assert final.result==already
            ticket=await db.scalar(select(Ticket))
            assert ticket.id in final.result["reply"]


def delivery_worker(url,barrier,queue):
    async def run():
        from app.services.background_jobs import tick,SimulatedAdapter
        engine=create_async_engine(url,poolclass=NullPool);factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            for _ in range(20):
                await asyncio.to_thread(barrier.wait,30)
                queue.put(await tick(factory,adapter=SimulatedAdapter()))
        finally: await engine.dispose()
    asyncio.run(run())


async def test_two_actual_job_workers_20_events(real_case):
    from app.services.background_jobs import enqueue
    url,factory=real_case
    async with factory() as db:
        for index in range(20):
            nid=f"synthetic-event-{index}"
            db.add(NotificationRecord(id=nid,event_type="synthetic.created",recipient="101",payload={"resource_id":nid},idempotency_key=nid))
            enqueue(db,"notification",nid,101,"notification:"+nid)
        await db.commit()
    ctx=multiprocessing.get_context("spawn");barrier=ctx.Barrier(2);queue=ctx.Queue()
    workers=[ctx.Process(target=delivery_worker,args=(url,barrier,queue)) for _ in range(2)]
    for worker in workers: worker.start()
    done=await asyncio.to_thread(lambda:[queue.get(timeout=60) for _ in range(40)])
    for worker in workers:
        await asyncio.to_thread(worker.join,10)
        assert worker.exitcode==0
    assert sum(done)==20
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(SimulatedDelivery))==20
        assert await db.scalar(select(func.count()).select_from(BackgroundJob).where(BackgroundJob.status=="succeeded"))==20


def delivery_crash_worker(url):
    async def run():
        from app.services.background_jobs import claim,perform,SimulatedAdapter
        engine=create_async_engine(url,poolclass=NullPool);factory=async_sessionmaker(engine,expire_on_commit=False)
        owned=await claim(factory)
        async def crash(): os._exit(24)
        await perform(factory,*owned,adapter=SimulatedAdapter(),after_send=crash)
    asyncio.run(run())


async def test_actual_worker_dies_after_delivery_before_ack(real_case):
    from app.services.background_jobs import enqueue,tick,SimulatedAdapter
    url,factory=real_case
    async with factory() as db:
        db.add(NotificationRecord(id="delivery-crash",event_type="synthetic",recipient="101",payload={"resource_id":"synthetic"},idempotency_key="delivery-crash"))
        enqueue(db,"notification","delivery-crash",101,"notification:delivery-crash")
        await db.commit()
    ctx=multiprocessing.get_context("spawn");worker=ctx.Process(target=delivery_crash_worker,args=(url,));worker.start()
    await asyncio.to_thread(worker.join,60);assert worker.exitcode==24
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(SimulatedDelivery))==1
        job=await db.scalar(select(BackgroundJob))
        job.lease_expires_at=datetime.utcnow()-timedelta(seconds=1)
        await db.commit()
    assert await tick(factory,adapter=SimulatedAdapter())
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(SimulatedDelivery))==1
        assert (await db.get(NotificationRecord,"delivery-crash")).status==NotificationStatus.SENT


async def test_mysql_migration_preserves_legacy_session_messages_and_outbox(real_case,monkeypatch):
    from alembic import command
    from alembic.config import Config
    url,factory=real_case
    monkeypatch.setattr(settings,"DATABASE_URL_OVERRIDE",url)
    async with factory() as db:
        db.add(Message(session_id="case-session",role="user",content="legacy synthetic"))
        db.add(NotificationRecord(id="legacy-event",event_type="synthetic",recipient="101",payload={"resource_id":"legacy"},idempotency_key="legacy-event"))
        await db.commit()
        await db.execute(text("ALTER TABLE sessions DROP COLUMN version,DROP COLUMN summary_cursor,DROP COLUMN summary_version,DROP COLUMN summary_policy,DROP COLUMN context_data,DROP COLUMN lease_token,DROP COLUMN lease_expires_at,DROP COLUMN deleted_at"))
        await db.execute(text("ALTER TABLE messages DROP COLUMN run_id"))
        await db.execute(text("ALTER TABLE notification_records DROP COLUMN is_simulated,DROP COLUMN provider_receipt"))
        await db.commit()
    assert url.rsplit("/",1)[-1].startswith("aicshub_m2_case_")
    async with factory() as db:
        for table in ("run_events","chat_runs","background_jobs","simulated_deliveries"):
            await db.execute(text(f"DROP TABLE `{table}`"))
        await db.commit()
    root=Path(__file__).resolve().parents[2];cfg=Config(str(root/"alembic.ini"));cfg.set_main_option("script_location",str(root/"alembic"))
    await asyncio.to_thread(command.stamp,cfg,"0007_m1_refund_actions")
    await asyncio.to_thread(command.upgrade,cfg,"head")
    async with factory() as db:
        assert (await db.get(Session,"case-session")).version==0
        assert (await db.scalar(select(Message))).content=="legacy synthetic"
        assert (await db.get(NotificationRecord,"legacy-event")).status==NotificationStatus.PENDING
        assert await db.scalar(select(func.count()).select_from(ChatRun))==0


async def test_actual_graph_rest_ws_refund_and_m1_safety_regression(real_case,monkeypatch):
    from acceptance.m1.real_services import test_mysql_real_rest_ws_and_refund_review
    await test_mysql_real_rest_ws_and_refund_review(real_case,monkeypatch)


async def test_real_redis_version_cas_rejects_20_delayed_writes():
    from app.agent.memory import SessionManager
    from app.agent.state import AgentState
    from app.utils.database import get_redis,close_redis
    sid="m2-version-"+uuid.uuid4().hex
    first,second=SessionManager(),SessionManager()
    try:
        for iteration in range(20):
            new=AgentState(session_id=sid,user_id=101,user_message="合成新状态",session_version=iteration*2+2,
                           messages=[{"role":"user","content":f"new-{iteration}"}])
            old=AgentState(session_id=sid,user_id=101,user_message="合成旧状态",session_version=iteration*2+1)
            await first.save_state(new);await second.save_state(old)
            restored=await second.get_state(sid)
            assert restored.session_version==new.session_version and restored.messages==new.messages
        redis=await get_redis()
        assert json.loads(await redis.get("session_state:v2:"+sid))["schema_version"]==2
    finally: await close_redis()


async def test_real_sql_context_recovers_after_owned_redis_restart(real_case,monkeypatch):
    from app.agent.memory import SessionManager
    from app.agent.state import AgentState
    from app.services.chat_service import ChatService
    from app.models.models import Order,RefundAction
    from app.services.refund_actions import prepare
    from app.utils.database import get_redis,close_redis
    import subprocess
    url,factory=real_case
    monkeypatch.setattr("app.utils.database.async_session",factory)
    async with factory() as db:
        db.add(Order(order_id="ORD920001",user_id=101,status="已签收",status_code="delivered",items=[],total_amount="0.10",received_at=datetime.utcnow()-timedelta(days=1)))
        db.add(Message(session_id="case-session",role="user",content="ORD920001 合成申请"))
        db.add(Message(session_id="case-session",role="assistant",content="请核对预览，尚未提交"))
        session=await db.get(Session,"case-session");session.version=2
        await db.commit()
    action=await prepare(factory,101,dict(user_id=101,session_id="case-session",order_id="ORD920001",reason="合成破损",type="refund"))
    manager=SessionManager();await manager.save_state(AgentState(session_id="case-session",user_id=101,user_message="",session_version=2))
    await close_redis()


    runtime=json.loads(Path(os.environ["M2_TEST_STATE"]).read_text(encoding="utf-8"))
    cid=runtime["containers"]["redis"]
    label=subprocess.check_output(["docker","inspect","--format",'{{index .Config.Labels "aicshub.test.owner"}}',cid],text=True).strip()
    assert label=="m2-closeout"
    # only the dedicated synthetic Redis is restarted; its persistence is disabled below.
    await asyncio.to_thread(subprocess.run,["docker","restart",cid],check=True,stdout=subprocess.DEVNULL)
    redis=await get_redis()
    await redis.delete("session_state:v2:case-session")  # exact test key only, never FLUSHDB
    fresh=SessionManager();monkeypatch.setattr("app.services.chat_service.session_manager",fresh)
    restored=await ChatService().hydrate_state("case-session",101,"继续",recover_from_db=True)
    assert restored.user_id==101 and restored.session_version==2
    assert any(m["content"]=="ORD920001 合成申请" for m in restored.messages)
    assert restored.collected_info["refund_preview"]["action_id"]==action["action_id"]
    assert not restored.collected_info.get("confirm_refund")
    await close_redis()



async def test_real_redis_lease_loss_blocks_tool_and_keeps_replacement_lock(real_case,monkeypatch):
    from app.agent.memory import SessionManager
    from app.services import run_execution as runs
    from app.schemas.schemas import ChatRequest
    from app.agent.tools import CreateTicketTool
    from app.agent.ticket_authorization import authorize_ticket
    from app.utils.database import get_redis,close_redis
    url,factory=real_case
    monkeypatch.setattr("app.utils.database.async_session",factory)
    manager=SessionManager()
    try:
        for index in range(20):
            sid=f"lease-{index}"
            async with factory() as db:
                db.add(Session(id=sid,user_id=101));await db.commit()
                run,_=await runs.accept(db,await db.get(User,101),ChatRequest(user_id=101,session_id=sid,message="合成失租问题",client_message_id="one"))
            async with manager.session_lock(sid) as owned:
                run,version=await runs._claim(factory,run.id,"sql-token",None,owned.redis_lock_token)
                redis=await get_redis()
                await redis.set("session_lock:"+sid,"new-worker",ex=60)
                payload=dict(user_id=101,session_id=sid,category="技术支持",content="合成失租问题",priority="medium",idempotency_key=f"chat-ticket:{sid}:{run.user_message_id}")
                result=await CreateTicketTool().execute(**payload,authorization=authorize_ticket(payload,run.user_message_id,101))
                assert not result["success"]
            assert await redis.get("session_lock:"+sid)=="new-worker"
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(Ticket))==0
    finally: await close_redis()


def late_worker(url,run_ids,ready,resume,queue):
    async def run():
        from app.services import run_execution as runs
        from app.agent.tools import CreateTicketTool
        from app.agent.ticket_authorization import authorize_ticket
        import app.utils.database as database
        engine=create_async_engine(url,poolclass=NullPool);factory=async_sessionmaker(engine,expire_on_commit=False)
        database.async_session=factory
        for rid in run_ids:
            current,version=await runs._claim(factory,rid,"old-owner",None)
            ready.set()
            await asyncio.to_thread(resume.wait,30);resume.clear()
            payload=dict(user_id=101,session_id=current.session_id,category="技术支持",content="合成迟到问题",priority="medium",
                         idempotency_key=f"chat-ticket:{current.session_id}:{current.user_message_id}")
            receipt=await CreateTicketTool().execute(**payload,authorization=authorize_ticket(payload,current.user_message_id,101))
            async with factory() as db:
                try:
                    await runs.assert_fence(db,current,"old-owner",version)
                    queue.put("unsafe-final-write")
                except Exception:
                    queue.put(receipt["success"])
        await engine.dispose()
    asyncio.run(run())


async def test_late_actual_worker_recovery_scan_and_tombstone_20_rounds(real_case):
    from app.schemas.schemas import ChatRequest
    from app.services import run_execution as runs
    url,factory=real_case
    ids=[]
    for index in range(20):
        async with factory() as db:
            sid=f"late-{index}";db.add(Session(id=sid,user_id=101));await db.commit()
            current,_=await runs.accept(db,await db.get(User,101),ChatRequest(user_id=101,session_id=sid,message="合成迟到问题",client_message_id="late"))
            ids.append(current.id)
    ctx=multiprocessing.get_context("spawn");ready,resume,queue=ctx.Event(),ctx.Event(),ctx.Queue()
    worker=ctx.Process(target=late_worker,args=(url,ids,ready,resume,queue));worker.start()
    for index,rid in enumerate(ids):
        assert await asyncio.to_thread(ready.wait,30);ready.clear()
        async with factory() as db:
            current=await db.get(ChatRun,rid)
            current.lease_expires_at=datetime.utcnow()-timedelta(seconds=1)
            session=await db.get(Session,current.session_id)
            session.version+=1;session.lease_token="replacement-owner"
            session.lease_expires_at=datetime.utcnow()+timedelta(seconds=60)
            await db.commit()
        await runs.recover(factory,rid)
        async with factory() as db:
            assert (await db.get(ChatRun,rid)).status=="running"  # scanner cannot steal replacement lease
            session=await db.get(Session,f"late-{index}")
            session.status="closed"
            if index%2==0: session.deleted_at=datetime.utcnow()
            await db.commit()
        for _ in range(3): await runs.recover(factory,rid)
        resume.set()
        assert await asyncio.to_thread(queue.get,True,30) is False
    await asyncio.to_thread(worker.join,20);assert worker.exitcode==0
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Ticket))==0
        assert await db.scalar(select(func.count()).select_from(ChatRun).where(ChatRun.status=="cancelled"))==20


def summary_crash_worker(url):
    async def run():
        from app.services.background_jobs import claim
        engine=create_async_engine(url,poolclass=NullPool);factory=async_sessionmaker(engine,expire_on_commit=False)
        assert await claim(factory,kind="summary")
        os._exit(25)
    asyncio.run(run())


async def test_summary_job_survives_actual_worker_crash(real_case):
    from app.services.background_jobs import enqueue,tick
    url,factory=real_case
    async with factory() as db:
        for index in range(30): db.add(Message(session_id="case-session",role="user",content=f"合成摘要{index}"))
        enqueue(db,"summary","case-session",101,"summary-crash")
        enqueue(db,"unregistered_index","synthetic-document-id",101,"index-unregistered")
        await db.commit()
    ctx=multiprocessing.get_context("spawn");worker=ctx.Process(target=summary_crash_worker,args=(url,));worker.start()
    await asyncio.to_thread(worker.join,60);assert worker.exitcode==25
    async with factory() as db:
        job=await db.scalar(select(BackgroundJob).where(BackgroundJob.kind=="summary"))
        job.lease_expires_at=datetime.utcnow()-timedelta(seconds=1);await db.commit()
    assert await tick(factory,kind="summary")
    assert await tick(factory,kind="unregistered_index")
    async with factory() as db:
        session=await db.get(Session,"case-session")
        assert session.summary_cursor==20 and session.summary_version==1
        assert (await db.scalar(select(BackgroundJob).where(BackgroundJob.kind=="summary"))).status=="succeeded"
        rejected=await db.scalar(select(BackgroundJob).where(BackgroundJob.kind=="unregistered_index"))
        assert rejected.status=="failed" and rejected.last_error=="handler_not_registered"


def long_background_worker(url, ready, resume):
    """合成长解析处理器：独立进程占用一个任务槽，真实SQL租约。"""
    async def run():
        from app.services.background_jobs import claim,perform
        engine=create_async_engine(url,poolclass=NullPool);factory=async_sessionmaker(engine,expire_on_commit=False)
        owned=await claim(factory,kind="synthetic_long_index")
        assert owned
        ready.set()
        assert await asyncio.to_thread(resume.wait,30)
        await perform(factory,*owned)  # M2未注册索引算法，必须明确失败。
        await engine.dispose()
    asyncio.run(run())


async def test_long_background_process_does_not_block_chat(real_case):
    from app.services.background_jobs import enqueue
    from app.services import run_execution as runs
    from app.agent.memory import SessionManager
    from app.schemas.schemas import ChatRequest
    url,factory=real_case
    async with factory() as db:
        enqueue(db,"synthetic_long_index","synthetic-document",101,"long-index")
        await db.commit()
        run,_=await runs.accept(db,await db.get(User,101),ChatRequest(user_id=101,session_id="case-session",message="合成并行聊天",client_message_id="during-index"))
    ctx=multiprocessing.get_context("spawn");ready,resume=ctx.Event(),ctx.Event()
    worker=ctx.Process(target=long_background_worker,args=(url,ready,resume));worker.start()
    try:
        assert await asyncio.to_thread(ready.wait,30)
        manager=SessionManager();manager._redis_available=False
        service=make_service(factory,manager)
        await asyncio.wait_for(runs.execute(factory,run.id,service,manager,None,False),5)
        async with factory() as db:
            assert (await db.get(ChatRun,run.id)).status=="succeeded"
            assert (await db.scalar(select(BackgroundJob).where(BackgroundJob.kind=="synthetic_long_index"))).status=="running"
    finally:
        resume.set();await asyncio.to_thread(worker.join,10)
    assert worker.exitcode==0
