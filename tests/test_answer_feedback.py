"""回答反馈权限、来源与持久化回归；仅临时数据库。"""
import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from app.models.models import Base, User, Session, Message, MessageRole
from app.routers.feedback import router
from app.security import get_current_user
from app.utils.database import get_db

@pytest.fixture
def feedback_api(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'feedback.db').as_posix()}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user = User(id=1, username="synthetic", role="user")
    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all([user, User(id=2, username="other", role="user")])
            await db.flush()
            db.add_all([Session(id="own", user_id=1), Session(id="other", user_id=2)])
            await db.flush()
            db.add_all([Message(id=1, session_id="own", role=MessageRole.ASSISTANT, content="合成回答"),
                        Message(id=2, session_id="other", role=MessageRole.ASSISTANT, content="他人回答"),
                        Message(id=3, session_id="own", role=MessageRole.USER, content="问题")])
            await db.commit()
    asyncio.run(setup())
    async def database():
        async with factory() as db:
            yield db
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        yield client, user
    asyncio.run(engine.dispose())

def payload(message=1, kind="like"):
    return dict(user_id=1, session_id="own", message_id=message, feedback_type=kind)

def test_vote_persistence_and_update(feedback_api):
    client, user = feedback_api
    first = client.post("/api/v1/feedback/submit", json=payload())
    assert first.status_code == 200
    second = client.post("/api/v1/feedback/submit", json=payload(kind="dislike"))
    assert second.json()["feedback_id"] == first.json()["feedback_id"]
    assert client.get("/api/v1/feedback/session/own").json()["data"] == {"1": "dislike"}
    assert client.get("/api/v1/feedback/records").status_code == 403
    user.role = "admin"
    result = client.get("/api/v1/feedback/records?feedback_type=dislike").json()["data"]
    assert result["total"] == 1
    assert result["records"][0]["answer"] == "合成回答"
    assert client.get("/api/v1/feedback/records?feedback_type=like").json()["data"]["total"] == 0

@pytest.mark.parametrize("message", [2, 3, 999])
def test_reject_invalid_answer(feedback_api, message):
    client, _ = feedback_api
    assert client.post("/api/v1/feedback/submit", json=payload(message)).status_code == 404

def test_owner_and_missing_message(feedback_api):
    client, _ = feedback_api
    assert client.get("/api/v1/feedback/session/other").status_code == 403
    assert client.post("/api/v1/feedback/submit", json=payload(None)).status_code == 400
    forged = payload(); forged["user_id"] = 2
    assert client.post("/api/v1/feedback/submit", json=forged).status_code == 403
