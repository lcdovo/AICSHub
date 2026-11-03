"""AC-04-05/06/07：隔离SQL、真实JWT与ASGI，图执行替身。"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.websockets import WebSocketDisconnect

from app.agent.memory import SessionManager
from app.config.config import settings
from app.models.models import Base, Message, Session, SessionStatus, User
from app.security import create_access_token, issue_refresh_token
from app.utils.database import get_db


@pytest.fixture
def auth_api(tmp_path, monkeypatch):
    import app.routers.chat as chat
    import app.routers.users as users_route

    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'auth.db').as_posix()}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    users = [User(id=101, username="synthetic-a", role="user", token_version=0, status=True),
             User(id=202, username="synthetic-b", role="user", token_version=0, status=True),
             User(id=999, username="synthetic-admin", role="admin", token_version=0, status=True)]

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all(users)
            await db.flush()
            db.add_all([Session(id="sa", user_id=101, status=SessionStatus.ACTIVE),
                        Session(id="sb", user_id=202, status=SessionStatus.ACTIVE)])
            await db.commit()

    asyncio.run(setup())
    manager = SessionManager()
    manager._redis_available = False
    monkeypatch.setattr(chat, "session_manager", manager)
    monkeypatch.setattr("app.services.chat_service.session_manager", manager)
    monkeypatch.setattr("app.utils.database.async_session", factory)
    calls = []

    async def process(state):
        calls.append(state.user_id)
        state.reply = "合成回复"
        return state

    async def process_stream(state):
        await process(state)
        yield {"type": "done", "reply": state.reply}

    monkeypatch.setattr(chat.chat_service, "process", process)
    monkeypatch.setattr(chat.chat_service, "process_stream", process_stream)
    monkeypatch.setattr(settings, "CHAT_RATE_LIMIT_PER_MINUTE", 100000)

    async def db_override():
        async with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(chat.router)
    app.include_router(users_route.router)
    app.dependency_overrides[get_db] = db_override
    with TestClient(app) as client:
        yield client, factory, manager, users, calls
    asyncio.run(engine.dispose())


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def change_user(factory, change, owner=101):
    async def mutate():
        async with factory() as db:
            user = await db.get(User, owner)
            if change == "delete":
                # 模拟认证主记录被移除；不触发ORM对子会话的置空操作。
                # 仅本次临时SQLite，保留会话以验证缺失主体不能继续读取它。
                await db.execute(delete(User).where(User.id == owner))
            elif change == "disabled_at":
                user.disabled_at = datetime.utcnow()
            elif change == "disabled":
                user.status = False
            elif change == "version":
                user.token_version += 1
            elif change == "role":
                user.role = "user"
            await db.commit()
    asyncio.run(mutate())


def message_count(factory):
    async def count():
        async with factory() as db:
            return await db.scalar(select(func.count()).select_from(Message))
    return asyncio.run(count())


def receive_turn(ws):
    events = []
    while True:
        event = ws.receive_json()
        events.append(event)
        if event["type"] in {"error", "stream_end"}:
            return events


@pytest.mark.parametrize("change", ["version", "disabled", "disabled_at", "delete"])
def test_revoked_identity_rejected_on_rest_and_new_ws(auth_api, change):
    client, factory, _, users, calls = auth_api
    token = create_access_token(users[0])
    change_user(factory, change)
    assert client.post("/api/v1/chat/send", headers=headers(token), json={"user_id": 101, "session_id": "sa", "message": "合成输入"}).status_code == 401
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(f"/api/v1/chat/stream?access_token={token}"):
            pass
    assert error.value.code == 4401
    assert calls == [] and message_count(factory) == 0


@pytest.mark.parametrize("change", ["version", "disabled", "disabled_at", "delete", "logout"])
def test_existing_ws_reauthorizes_next_round(auth_api, change):
    client, factory, _, users, calls = auth_api
    token = create_access_token(users[0])
    with client.websocket_connect(f"/api/v1/chat/stream?access_token={token}") as ws:
        ws.send_json({"user_id": 101, "session_id": "sa", "message": "第一轮"})
        assert receive_turn(ws)[-1]["type"] == "stream_end"
        assert message_count(factory) == 2
        if change == "logout":
            assert client.post("/api/v1/users/logout", headers=headers(token)).status_code == 200
        else:
            change_user(factory, change)
        ws.send_json({"user_id": 101, "session_id": "sa", "message": "不应执行的第二轮"})
        events = receive_turn(ws)
        assert events[0]["type"] == "error" and events[0]["code"] == 401
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_json()
        assert error.value.code == 4401
        assert calls == [101] and message_count(factory) == 2


@pytest.mark.parametrize("variant", ["expired", "signature", "refresh", "no_exp", "no_ver", "bad_sub", "bool_ver"])
def test_invalid_token_contract_is_equal_for_rest_and_ws(auth_api, variant):
    client, factory, _, _, calls = auth_api
    claims = {"sub": "101", "type": "access", "ver": 0, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    key = settings.AUTH_SECRET_KEY
    if variant == "expired":
        claims["exp"] = datetime.now(timezone.utc) - timedelta(seconds=30)
    elif variant == "signature":
        key = "synthetic-wrong-key-at-least-32-characters"
    elif variant == "refresh":
        claims["type"] = "refresh"
    elif variant == "no_exp":
        del claims["exp"]
    elif variant == "no_ver":
        del claims["ver"]
    elif variant == "bad_sub":
        claims["sub"] = "not-a-user-id"
    elif variant == "bool_ver":
        claims["ver"] = False
    token = jwt.encode(claims, key, algorithm="HS256")
    assert client.post("/api/v1/chat/send", headers=headers(token), json={"user_id": 101, "message": "合成输入"}).status_code == 401
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(f"/api/v1/chat/stream?access_token={token}"):
            pass
    assert error.value.code == 4401
    assert calls == [] and message_count(factory) == 0


def test_token_expiry_during_open_connection(auth_api, monkeypatch):
    client, factory, _, users, calls = auth_api
    token = create_access_token(users[0])
    future = datetime.now(timezone.utc) + timedelta(days=2)

    class FutureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return future if tz else future.replace(tzinfo=None)

    with client.websocket_connect(f"/api/v1/chat/stream?access_token={token}") as ws:
        monkeypatch.setattr("jwt.api_jwt.datetime", FutureDateTime)
        ws.send_json({"user_id": 101, "session_id": "sa", "message": "已过期"})
        events = receive_turn(ws)
        assert events[0]["type"] == "error" and events[0]["code"] == 401
    assert calls == [] and message_count(factory) == 0


def test_admin_role_is_read_from_database(auth_api):
    client, factory, _, users, _ = auth_api
    token = create_access_token(users[2])
    assert client.get("/api/v1/users/", headers=headers(token)).status_code == 200
    change_user(factory, "role", owner=999)
    assert client.get("/api/v1/users/", headers=headers(token)).status_code == 403


def test_revoked_refresh_cannot_mint_access_after_logout(auth_api):
    client, factory, _, users, _ = auth_api
    token = create_access_token(users[0])

    async def issue():
        async with factory() as db:
            refresh, _ = await issue_refresh_token(db, await db.get(User, 101))
            await db.commit()
            return refresh

    refresh = asyncio.run(issue())
    assert client.post("/api/v1/users/logout", headers=headers(token)).status_code == 200
    assert client.post("/api/v1/users/refresh", json={"refresh_token": refresh}).status_code == 401


@pytest.mark.parametrize("transport", ["rest", "ws"])
def test_revocation_while_waiting_for_lock_prevents_message_write(auth_api, monkeypatch, transport):
    client, factory, manager, users, calls = auth_api
    token = create_access_token(users[0])

    @asynccontextmanager
    async def revoke_before_acquisition(session_id):
        async with factory() as db:
            user = await db.get(User, 101)
            user.token_version += 1
            await db.commit()

        async def assert_owner():
            return None

        yield assert_owner

    monkeypatch.setattr(manager, "session_lock", revoke_before_acquisition)
    if transport == "ws":
        with client.websocket_connect(f"/api/v1/chat/stream?access_token={token}") as ws:
            ws.send_json({"user_id": 101, "session_id": "sa", "message": "等待期间已撤销"})
            events = receive_turn(ws)
            assert events[0]["type"] == "error" and events[0]["code"] == 401
    else:
        assert client.post("/api/v1/chat/send", headers=headers(token), json={"user_id": 101, "session_id": "sa", "message": "等待期间已撤销"}).status_code == 401
    assert calls == [] and message_count(factory) == 0


def test_valid_token_and_foreign_identity_rejection_keep_ws_usable(auth_api):
    client, factory, _, users, calls = auth_api
    token = create_access_token(users[0])
    with client.websocket_connect("/api/v1/chat/stream", headers={"Authorization": f"bearer {token}"}) as ws:
        ws.send_json({"user_id": 202, "session_id": "sb", "message": "伪造主体"})
        assert receive_turn(ws)[0]["code"] == 403
        ws.send_json({"user_id": 101, "session_id": "sb", "message": "他人会话"})
        assert receive_turn(ws)[0]["code"] == 403
        assert message_count(factory) == 0
        ws.send_json({"user_id": 101, "session_id": "sa", "message": "合法请求"})
        assert receive_turn(ws)[-1]["type"] == "stream_end"
    assert calls == [101] and message_count(factory) == 2


def test_refresh_rotation_rejects_old_token_and_new_access_can_chat(auth_api):
    client, factory, _, _, calls = auth_api

    async def issue():
        async with factory() as db:
            token, _ = await issue_refresh_token(db, await db.get(User, 101))
            await db.commit()
            return token

    old = asyncio.run(issue())
    first = client.post("/api/v1/users/refresh", json={"refresh_token": old})
    assert first.status_code == 200
    assert client.post("/api/v1/users/refresh", json={"refresh_token": old}).status_code == 401
    access = first.json()["data"]["access_token"]
    assert client.post("/api/v1/chat/send", headers=headers(access), json={"user_id": 101, "session_id": "sa", "message": "刷新后聊天"}).status_code == 200
    assert calls == [101]


def test_admin_demotion_during_rest_lock_wait_blocks_foreign_action(auth_api, monkeypatch):
    client, factory, manager, users, calls = auth_api
    token = create_access_token(users[2])

    @asynccontextmanager
    async def demote_before_acquisition(session_id):
        async with factory() as db:
            user = await db.get(User, 999)
            user.role = "user"
            await db.commit()

        async def assert_owner():
            return None

        yield assert_owner

    monkeypatch.setattr(manager, "session_lock", demote_before_acquisition)
    result = client.post("/api/v1/chat/send", headers=headers(token), json={"user_id": 202, "session_id": "sb", "message": "管理员权限已撤销"})
    assert result.status_code == 403
    assert calls == [] and message_count(factory) == 0


def test_disabled_at_refresh_is_rejected_without_rotating(auth_api):
    client, factory, _, _, _ = auth_api

    async def issue():
        async with factory() as db:
            token, _ = await issue_refresh_token(db, await db.get(User, 101))
            await db.commit()
            return token

    refresh = asyncio.run(issue())
    change_user(factory, "disabled_at")
    assert client.post("/api/v1/users/refresh", json={"refresh_token": refresh}).status_code == 401
