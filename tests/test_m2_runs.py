import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, func
from fastapi import HTTPException

from app.agent.memory import SessionManager
from app.agent.state import AgentState
from app.models.models import ChatRun,RunEvent,Message,Session,User,Ticket,OperationAudit,RefundAction
from app.schemas.schemas import ChatRequest
from app.services import run_execution as runs
from app.services.chat_service import ChatService
from tests.test_ticket_idempotency import api_context
from tests.test_chat_auth import auth_api, headers, receive_turn


def test_client_id_replay_conflict_authorization_and_event_cursor(auth_api):
    client,factory,_,users,calls=auth_api
    from app.security import create_access_token
    auth=headers(create_access_token(users[0]))
    body=dict(user_id=101,session_id="sa",message="合成问题",client_message_id="stable-client-id")
    original=client.post("/api/v1/chat/send",headers=auth,json=body)
    assert original.status_code==200,original.text
    data=original.json()
    for _ in range(10):
        assert client.post("/api/v1/chat/send",headers=auth,json=body).json()==data
    assert client.post("/api/v1/chat/send",headers=auth,json={**body,"message":"参数变化"}).status_code==409
    rid=data["run_id"]
    events=client.get(f"/api/v1/chat/runs/{rid}/events",headers=auth).json()["data"]
    assert [e["sequence"] for e in events]==list(range(1,len(events)+1))
    assert all(e["run_id"]==rid and e["session_id"]=="sa" and e["schema_version"]==1 for e in events)
    assert events[-1]["type"]=="stream_end" and next(e for e in events if e["type"]=="done")["reply"]==data["reply"]
    assert client.get(f"/api/v1/chat/runs/{rid}/events?after={events[-1]['sequence']}",headers=auth).json()["data"]==[]
    other=headers(create_access_token(users[1]))
    assert client.get(f"/api/v1/chat/runs/{rid}",headers=other).status_code==403
    assert client.get(f"/api/v1/chat/runs/{rid}/events",headers=other).status_code==403
    resolved=client.get("/api/v1/chat/sessions/sa/runs/stable-client-id",headers=auth)
    assert resolved.status_code==200 and resolved.json()["data"]["run_id"]==rid
    async def count():
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(ChatRun))==1
            assert await db.scalar(select(func.count()).select_from(Message))==2
    asyncio.run(count())
    assert calls==[101]


def test_ws_final_events_reference_already_committed_result(auth_api):
    client,factory,_,users,_=auth_api
    from app.security import create_access_token
    auth=headers(create_access_token(users[0]))
    with client.websocket_connect("/api/v1/chat/stream",headers=auth) as ws:
        ws.send_json(dict(user_id=101,session_id="sa",message="合成输入",client_message_id="ws-id"))
        events=receive_turn(ws)
    rid=events[0]["run_id"]
    result=client.get("/api/v1/chat/runs/"+rid,headers=auth).json()["data"]
    assert result["status"]=="succeeded"
    assert result["result"]["message_id"]==events[-1]["message_id"]
    assert client.get("/api/v1/chat/history/sa",headers=auth).json()["data"]["messages"][-1]["content"]==result["result"]["reply"]


def test_cancel_waiting_run_only_cancels_its_action(auth_api):
    from app.security import create_access_token
    client,factory,_,users,_=auth_api
    auth=headers(create_access_token(users[0]))
    async def seed():
        async with factory() as db:
            for aid in ("target","other"):
                db.add(RefundAction(id=aid,user_id=101,actor_id=101,session_id="sa",order_id="SYNTHETIC",
                    type="refund",reason="合成破损",amount="0.10",currency="CNY",expires_at=datetime.utcnow()+timedelta(minutes=15)))
            db.add(ChatRun(id="waiting",user_id=101,actor_id=101,session_id="sa",client_message_id="waiting",request_hash="synthetic",
                request_data={"message":"合成申请"},action_id="target",status="awaiting_confirmation"))
            await db.commit()
    asyncio.run(seed())
    assert client.post("/api/v1/chat/runs/waiting/cancel",headers=auth).status_code==200
    async def inspect():
        async with factory() as db:
            assert (await db.get(RefundAction,"target")).status=="cancelled"
            assert (await db.get(RefundAction,"other")).status=="pending"
            assert (await db.get(ChatRun,"waiting")).status=="cancelled"
    asyncio.run(inspect())


async def setup_run(factory,cid="synthetic-run"):
    async with factory() as db:
        return (await runs.accept(db,await db.get(User,101),ChatRequest(user_id=101,session_id="session-101",message="合成问题",client_message_id=cid)))[0]


async def test_orphan_before_graph_is_explicit_failure_without_duplicate_message(tmp_path):
    async with api_context(tmp_path/"orphan.db") as (_,factory):
        run=await setup_run(factory)
        claimed,_=await runs._claim(factory,run.id,"old-worker",None)
        async with factory() as db:
            (await db.get(ChatRun,run.id)).lease_expires_at=datetime.utcnow()-timedelta(seconds=1)
            (await db.get(Session,run.session_id)).lease_expires_at=datetime.utcnow()-timedelta(seconds=1)
            await db.commit()
        await runs.recover(factory,run.id)
        await runs.recover(factory,run.id)
        async with factory() as db:
            assert (await db.get(ChatRun,run.id)).status=="failed"
            assert await db.scalar(select(func.count()).select_from(Message))==1


async def test_committed_tool_receipt_recovers_message_without_reexecuting(tmp_path,monkeypatch):
    from app.agent.tools import CreateTicketTool
    from app.agent.ticket_authorization import authorize_ticket
    async with api_context(tmp_path/"receipt.db") as (_,factory):
        monkeypatch.setattr("app.utils.database.async_session",factory)
        run=await setup_run(factory)
        claimed,_=await runs._claim(factory,run.id,"old-worker",None)
        payload=dict(user_id=101,session_id=run.session_id,category="技术支持",content="合成问题",priority="medium",
                     idempotency_key=f"chat-ticket:{run.session_id}:{claimed.user_message_id}")
        first=await CreateTicketTool().execute(**payload,authorization=authorize_ticket(payload,claimed.user_message_id,101))
        assert first["success"]
        async with factory() as db:
            (await db.get(ChatRun,run.id)).lease_expires_at=datetime.utcnow()-timedelta(seconds=1)
            (await db.get(Session,run.session_id)).lease_expires_at=datetime.utcnow()-timedelta(seconds=1)
            await db.commit()
        for _ in range(5): await runs.recover(factory,run.id)
        async with factory() as db:
            current=await db.get(ChatRun,run.id)
            assert current.status=="succeeded" and first["data"]["ticket_id"] in current.result["reply"]
            assert await db.scalar(select(func.count()).select_from(Ticket))==1
            assert await db.scalar(select(func.count()).select_from(Message))==2
            original=await db.get(Message,claimed.user_message_id)
            with pytest.raises(HTTPException): await runs.guard_write(db,original)


async def test_late_worker_and_deleted_session_cannot_commit(tmp_path):
    async with api_context(tmp_path/"fence.db") as (_,factory):
        run=await setup_run(factory)
        claimed,version=await runs._claim(factory,run.id,"old-worker",None)
        async with factory() as db:
            session=await db.get(Session,run.session_id)
            session.lease_token="new-worker"
            await db.commit()
        async with factory() as db:
            with pytest.raises(Exception,match="失效"):
                await runs.assert_fence(db,claimed,"old-worker",version)
        async with factory() as db:
            session=await db.get(Session,run.session_id)
            session.deleted_at=datetime.utcnow()
            (await db.get(ChatRun,run.id)).lease_expires_at=datetime.utcnow()-timedelta(seconds=1)
            await db.commit()
        await runs.recover(factory,run.id)
        async with factory() as db:
            assert (await db.get(ChatRun,run.id)).status=="cancelled"
            assert await db.scalar(select(func.count()).select_from(Message))==1


async def test_final_commit_failure_never_publishes_completed_event(tmp_path,monkeypatch):
    async with api_context(tmp_path/"final.db") as (_,factory):
        monkeypatch.setattr("app.utils.database.async_session",factory)
        manager=SessionManager();manager._redis_available=False
        monkeypatch.setattr("app.services.chat_service.session_manager",manager)
        async def process(state): state.reply="未提交答案";return state
        service=ChatService(SimpleNamespace(run=process))
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.exc import OperationalError
        original_commit=AsyncSession.commit
        async def fail_final_commit(db):
            if any(isinstance(item,ChatRun) and item.result and item.status in runs.FINAL for item in db.dirty):
                await db.flush()
                raise OperationalError("synthetic-final-commit",{},RuntimeError("synthetic fault"))
            await original_commit(db)
        monkeypatch.setattr(AsyncSession,"commit",fail_final_commit)
        run=await setup_run(factory)
        await runs.execute(factory,run.id,service,manager,None,False)
        async with factory() as db:
            assert (await db.get(ChatRun,run.id)).status=="reconciling"
            assert await db.scalar(select(func.count()).select_from(Message))==1
            events=(await db.execute(select(RunEvent))).scalars().all()
            assert not any(event.event_type in {"token","done","stream_end"} for event in events)


async def test_slow_transport_is_bounded_and_does_not_cancel_detached_run(tmp_path,monkeypatch):
    from app.routers import chat
    from app.security import create_access_token
    async with api_context(tmp_path/"slow.db") as (_,factory):
        monkeypatch.setattr("app.utils.database.async_session",factory)
        manager=SessionManager();manager._redis_available=False
        monkeypatch.setattr(chat,"session_manager",manager)
        monkeypatch.setattr("app.services.chat_service.session_manager",manager)
        monkeypatch.setattr(chat,"WS_SEND_TIMEOUT_SECONDS",0.02)
        async def process_stream(state):
            await asyncio.sleep(0.05)
            state.reply="可查询的已持久结果"
            yield {"type":"done","reply":state.reply}
        monkeypatch.setattr(chat.chat_service,"process_stream",process_stream)
        async with factory() as db:
            token=create_access_token(await db.get(User,101))
        class SlowSocket:
            headers={"authorization":"Bearer "+token}
            query_params={}
            async def accept(self): pass
            async def receive_text(self):
                import json
                return json.dumps(dict(user_id=101,session_id="session-101",client_message_id="slow-one",message="合成问题"))
            async def send_json(self,data): await asyncio.sleep(10)
            async def close(self,**kwargs): pass
        await asyncio.wait_for(chat.websocket_chat_stream(SlowSocket()),1)
        async with factory() as db:
            other,_=await runs.accept(db,await db.get(User,202),ChatRequest(user_id=202,session_id="session-202",
                message="其他用户不受慢连接影响",client_message_id="other-user"))
        runs.start(factory,other,chat.chat_service,manager,None,True)
        second=await asyncio.wait_for(runs.wait(factory,other.id),1)
        assert second["session_id"]=="session-202"
        for _ in range(100):
            async with factory() as db:
                run=await db.scalar(select(ChatRun).where(ChatRun.client_message_id=="slow-one"))
                if run and run.status=="succeeded": break
            await asyncio.sleep(0.02)
        assert run.status=="succeeded" and run.result["reply"]=="可查询的已持久结果"


async def test_cancelled_accepted_run_cannot_write_user_message(tmp_path):
    async with api_context(tmp_path/"cancel-before.db") as (_,factory):
        run=await setup_run(factory)
        async with factory() as db:
            (await db.get(ChatRun,run.id)).status="cancelled";await db.commit()
        with pytest.raises(Exception): await runs._claim(factory,run.id,"late-worker",None)
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(Message))==0
            assert (await db.get(Session,run.session_id)).lease_token is None


async def test_refund_committed_receipt_recovery_does_not_submit_again(tmp_path,monkeypatch):
    from app.models.models import Order,RefundApplication
    from app.services.refund_actions import prepare
    from app.services.refund_application import submit
    async with api_context(tmp_path/"refund-recovery.db") as (_,factory):
        async with factory() as db:
            db.add(Order(order_id="ORD900001",user_id=101,status="已发货",status_code="shipped",total_amount="0.10",items=[]))
            await db.commit()
        payload=dict(user_id=101,session_id="session-101",order_id="ORD900001",type="refund",reason="合成破损")
        action=await prepare(factory,101,payload)
        async with factory() as db:
            run,_=await runs.accept(db,await db.get(User,101),ChatRequest(user_id=101,session_id="session-101",message="确认",
                action_id=action["action_id"],action_version=action["version"],client_message_id="refund-crash"))
        claimed,_=await runs._claim(factory,run.id,"old-refund-worker",None)
        async with factory() as db:
            (await db.get(Session,run.session_id)).context_data={"collected_info":{"refund_preview":{"action_id":action["action_id"]}},"pending_confirmations":["apply_refund"]}
            await db.commit()
        result=await submit(factory,{**payload,"action_id":action["action_id"],"action_version":action["version"],
            "idempotency_key":"refund-action:"+action["action_id"]},claimed.user_message_id,101)
        async with factory() as db:
            (await db.get(ChatRun,run.id)).lease_expires_at=datetime.utcnow()-timedelta(seconds=1)
            (await db.get(Session,run.session_id)).lease_expires_at=datetime.utcnow()-timedelta(seconds=1)
            await db.commit()
        for _ in range(3): await runs.recover(factory,run.id)
        async with factory() as db:
            current=await db.get(ChatRun,run.id)
            assert current.status=="succeeded" and result["refund_id"] in current.result["reply"]
            assert "0.10" in current.result["reply"] and "模拟" in current.result["reply"]
            projection=(await db.get(Session,run.session_id)).context_data
            assert projection["pending_confirmations"]==[] and "refund_preview" not in projection["collected_info"]
            assert await db.scalar(select(func.count()).select_from(RefundApplication))==1
            assert await db.scalar(select(func.count()).select_from(Message))==2
