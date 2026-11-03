"""AC-03-02/03、AC-05-02：隔离SQL与Redis替身验证显式会话创建。"""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI, Header
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent.memory import SessionLeaseError, SessionManager
from app.agent.state import AgentState
from app.models.models import Base, Message, OperationAudit, Session, User
from app.security import create_access_token, get_current_user
from app.utils.database import get_db
from tests.test_chat_turns import FakeRedis


class SnapshotRedis(FakeRedis):
    async def setex(self, key, ttl, value):
        self.values[key] = value


@pytest.fixture
def session_api(tmp_path, monkeypatch):
    import app.routers.chat as chat
    import app.routers.sessions as sessions

    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'session.db').as_posix()}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    users = [User(id=101, username="synthetic-a", role="user", token_version=0, status=True),
             User(id=202, username="synthetic-b", role="user", token_version=0, status=True),
             User(id=999, username="synthetic-admin", role="admin", token_version=0, status=True)]

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all(users)
            await db.commit()

    asyncio.run(setup())
    redis = SnapshotRedis()
    manager = SessionManager()
    manager._redis_available = True
    monkeypatch.setattr("app.agent.memory.get_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr(chat, "session_manager", manager)
    monkeypatch.setattr("app.services.chat_service.session_manager", manager)
    monkeypatch.setattr("app.utils.database.async_session", factory)
    # 新共享创建服务也引用同一个管理器。
    monkeypatch.setattr("app.services.session_creation.session_manager", manager)

    async def db_override():
        async with factory() as db:
            yield db

    async def auth_override(x_test_actor: int = Header(default=101), db: AsyncSession = Depends(get_db)):
        return await db.get(User, x_test_actor)

    async def process(state):
        state.reply = "合成客服回复"
        return state

    async def process_stream(state):
        state.reply = "合成客服回复"
        yield {"type": "done", "reply": state.reply}

    monkeypatch.setattr(chat.chat_service, "process", process)
    monkeypatch.setattr(chat.chat_service, "process_stream", process_stream)
    app = FastAPI()
    app.include_router(sessions.router)
    app.include_router(chat.router)
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = auth_override
    with TestClient(app) as client:
        yield client, factory, manager, redis, users
    asyncio.run(engine.dispose())


def create(client, endpoint, owner=101, actor=101):
    if endpoint == "sessions":
        return client.post("/api/v1/sessions/", json={"user_id": owner}, headers={"X-Test-Actor": str(actor)})
    return client.post(f"/api/v1/chat/sessions?user_id={owner}", headers={"X-Test-Actor": str(actor)})


@pytest.mark.parametrize("endpoint", ["sessions", "chat"])
def test_create_snapshot_then_immediately_chat(session_api, endpoint):
    client, factory, manager, redis, _ = session_api
    result = create(client, endpoint)
    assert result.status_code == 200, result.text
    session_id = result.json()["data"]["id"]
    snapshot = json.loads(redis.values[f"session:{session_id}"])
    assert isinstance(snapshot, dict)
    assert snapshot["schema_version"] == 1
    assert snapshot["session_id"] == session_id and snapshot["user_id"] == 101
    state = asyncio.run(manager.get_state(session_id))
    assert state.messages == [] and state.pending_confirmations == []
    reply = client.post("/api/v1/chat/send", json={"user_id": 101, "session_id": session_id, "message": "你好"})
    assert reply.status_code == 200, reply.text
    assert reply.json()["reply"] == "合成客服回复"

    async def inspect():
        async with factory() as db:
            return (await db.get(Session, session_id)).user_id, await db.scalar(select(func.count()).select_from(Message))

    assert asyncio.run(inspect()) == (101, 2)


@pytest.mark.parametrize("endpoint", ["sessions", "chat"])
def test_admin_creates_for_owner_with_actor_audit(session_api, endpoint):
    client, factory, _, _, _ = session_api
    result = create(client, endpoint, actor=999)
    assert result.status_code == 200
    session_id = result.json()["data"]["id"]

    async def inspect():
        async with factory() as db:
            item = await db.get(Session, session_id)
            audit = await db.scalar(select(OperationAudit).where(OperationAudit.resource_id == session_id))
            assert item.user_id == 101
            assert audit.operation == "create_session" and audit.user_id == 101
            assert audit.request_payload["actor_id"] == 999
            assert audit.request_payload["owner_id"] == 101

    asyncio.run(inspect())
    assert client.get(f"/api/v1/sessions/{session_id}", headers={"X-Test-Actor": "202"}).status_code == 403


@pytest.mark.parametrize("endpoint", ["sessions", "chat"])
@pytest.mark.parametrize("owner,actor,expected", [(202, 101, 403), (555, 999, 404)])
def test_invalid_owner_never_creates_session(session_api, endpoint, owner, actor, expected):
    client, factory, _, redis, _ = session_api
    assert create(client, endpoint, owner, actor).status_code == expected

    async def inspect():
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(Session)) == 0
            assert await db.scalar(select(func.count()).select_from(OperationAudit)) == 0

    asyncio.run(inspect())
    assert redis.values == {}


@pytest.mark.parametrize("value", [101, [], None, {"schema_version": 999, "session_id": "s1", "user_id": 101},
                                  {"session_id": "other", "user_id": 101},
                                  {"schema_version": True, "session_id": "s1", "user_id": 101},
                                  {"session_id": "s1", "user_id": True},
                                  {"session_id": "s1", "user_id": 0}])
async def test_invalid_shared_snapshot_fails_closed_and_is_preserved(monkeypatch, value):
    redis = SnapshotRedis()
    manager = SessionManager()
    manager._redis_available = True
    redis.values["session:s1"] = json.dumps(value)
    monkeypatch.setattr("app.agent.memory.get_redis", AsyncMock(return_value=redis))
    with pytest.raises(SessionLeaseError):
        await manager.get_state("s1")
    assert redis.values["session:s1"] == json.dumps(value)


async def test_versionless_object_remains_readable(monkeypatch):
    redis = SnapshotRedis()
    manager = SessionManager()
    manager._redis_available = True
    redis.values["session:s1"] = json.dumps({"session_id": "s1", "user_id": 101, "messages": [{"role": "user", "content": "历史"}]})
    monkeypatch.setattr("app.agent.memory.get_redis", AsyncMock(return_value=redis))
    assert (await manager.get_state("s1")).messages[0]["content"] == "历史"


@pytest.mark.parametrize("endpoint", ["sessions", "chat"])
def test_create_then_immediate_websocket_chat(session_api, endpoint):
    client, _, manager, _, users = session_api
    session_id = create(client, endpoint).json()["data"]["id"]
    token = create_access_token(users[0])
    with client.websocket_connect(f"/api/v1/chat/stream?access_token={token}") as ws:
        ws.send_json({"session_id": session_id, "user_id": 101, "message": "合成输入"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] in {"error", "stream_end"}:
                break
    assert events[-1]["type"] == "stream_end", events
    assert next(event["reply"] for event in events if event["type"] == "done") == "合成客服回复"
    state = asyncio.run(manager.get_state(session_id))
    assert state.user_id == 101 and len(state.messages) == 2


@pytest.mark.parametrize("endpoint", ["sessions", "chat"])
@pytest.mark.parametrize("failure", ["unavailable", "write"])
def test_redis_initialization_failure_keeps_committed_session_and_can_chat(session_api, monkeypatch, endpoint, failure):
    client, factory, manager, redis, _ = session_api
    original_set = redis.set
    if failure == "unavailable":
        manager._redis_available = False
    else:
        monkeypatch.setattr(redis, "set", AsyncMock(side_effect=RuntimeError("synthetic-cache-secret")))
    result = create(client, endpoint)
    assert result.status_code == 200 and "synthetic-cache-secret" not in result.text
    session_id = result.json()["data"]["id"]

    async def inspect():
        async with factory() as db:
            assert (await db.get(Session, session_id)).user_id == 101
            assert await db.scalar(select(func.count()).select_from(OperationAudit)) == 1

    asyncio.run(inspect())
    # 恢复替身后继续已有会话，不能因缓存初始化失败要求创建第二个会话。
    monkeypatch.setattr(redis, "set", original_set)
    manager._redis_available = True
    reply = client.post("/api/v1/chat/send", json={"user_id": 101, "session_id": session_id, "message": "你好"})
    assert reply.status_code == 200, reply.text


async def test_delayed_initialization_never_overwrites_existing_state(monkeypatch):
    redis = SnapshotRedis()
    manager = SessionManager()
    manager._redis_available = True
    saved = AgentState(session_id="s1", user_id=101, user_message="历史")
    saved.pending_confirmations = ["apply_refund"]
    redis.values["session:s1"] = json.dumps(manager._serialize_state(saved))
    original = redis.values["session:s1"]
    monkeypatch.setattr("app.agent.memory.get_redis", AsyncMock(return_value=redis))
    await manager.initialize_state("s1", 101)
    assert redis.values["session:s1"] == original
    assert manager._state_cache == {}


@pytest.mark.parametrize("endpoint", ["sessions", "chat"])
def test_database_failure_never_writes_snapshot_or_partial_audit(session_api, monkeypatch, endpoint):
    client, factory, _, redis, _ = session_api

    async def fail_commit(db):
        await db.flush()
        raise RuntimeError("synthetic commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            create(client, endpoint)

    async def inspect():
        async with factory() as db:
            for model in (Session, OperationAudit):
                assert await db.scalar(select(func.count()).select_from(model)) == 0

    asyncio.run(inspect())
    assert redis.values == {}


async def test_hydration_rejects_snapshot_from_another_owner(monkeypatch):
    from app.services.chat_service import ChatService
    saved = AgentState(session_id="s1", user_id=202, user_message="合成私有消息")
    manager = SessionManager()
    monkeypatch.setattr(manager, "get_state", AsyncMock(return_value=saved))
    monkeypatch.setattr("app.services.chat_service.session_manager", manager)
    with pytest.raises(SessionLeaseError, match="归属"):
        await ChatService().hydrate_state("s1", 101, "你好")
