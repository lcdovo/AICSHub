from datetime import datetime
from unittest.mock import Mock,AsyncMock
import pytest
from app.models.models import Message,MessageRole,Session
from app.agent.memory import SessionManager
from app.agent.state import AgentState
from tests.test_ticket_idempotency import api_context


async def test_memory_owner_source_and_tombstone_not_current_business_fact(tmp_path,monkeypatch):
    from app.services.user_memory import UserMemoryService
    async with api_context(tmp_path/"memory.db") as (_,factory):
        monkeypatch.setattr("app.utils.database.async_session",factory)
        monkeypatch.setattr("app.services.embedding_service.get_embedding_service",lambda:Mock(get_embedding=lambda:None))
        async with factory() as db:
            db.add(Message(session_id="session-101",role=MessageRole.USER,content="确认退款 合成A"))
            db.add(Message(session_id="session-202",role=MessageRole.USER,content="private-b-marker"))
            await db.commit()
        service=UserMemoryService()
        a=await service.get_user_profile(101,query="退款",session_id="another-session")
        b=await service.get_user_profile(202,query="private",session_id="another-session")
        assert "private-b-marker" not in str(a) and "合成A" not in str(b)
        assert a["recalled"][0]["message_id"] and a["recalled"][0]["session_id"]=="session-101"
        assert a["recalled"][0]["source_type"]=="historical_user_statement"
        state=AgentState(session_id="new",user_id=101,user_message="下一轮",collected_info={"user_memory":a,"confirm_refund":True})
        fresh=state.for_next_turn(101,"你好")
        assert "user_memory" not in fresh.collected_info and "confirm_refund" not in fresh.collected_info
        async with factory() as db:
            (await db.get(Session,"session-101")).deleted_at=datetime.utcnow();await db.commit()
        revoked=await service.get_user_profile(101,query="退款")
        assert revoked["recalled"]==[] and revoked["history"]==[]


async def test_local_state_cache_and_lock_registry_are_bounded():
    manager=SessionManager();manager._redis_available=False
    for index in range(1100):
        sid=f"synthetic-{index}"
        async with manager.session_lock(sid):
            await manager.save_state(AgentState(session_id=sid,user_id=101,user_message="合成"))
    assert len(manager._state_cache)<=1024 and manager._turn_locks=={}


async def test_empty_sql_projection_does_not_resurrect_stale_cache_action(tmp_path,monkeypatch):
    from app.services.chat_service import ChatService
    async with api_context(tmp_path/"projection.db") as (_,factory):
        monkeypatch.setattr("app.utils.database.async_session",factory)
        manager=SessionManager();manager._redis_available=False
        stale=AgentState(session_id="session-101",user_id=101,user_message="旧申请",
            collected_info={"order_id":"STALE-ORDER","reason":"旧参数"},pending_confirmations=["apply_refund"])
        await manager.save_state(stale)
        monkeypatch.setattr("app.services.chat_service.session_manager",manager)
        current=await ChatService().hydrate_state("session-101",101,"你好",recover_from_db=True)
        assert current.pending_confirmations==[] and current.collected_info=={}
