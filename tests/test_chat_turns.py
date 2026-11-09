"""Turn boundaries, validated answer reuse and session ownership regressions."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import AgentGraph, _stream_event_queue
from app.agent.memory import SessionManager, SessionBusyError, SessionLeaseError
from app.agent.state import AgentState, AgentNode
from app.agent.validation import ValidationResult
from app.config.config import settings
from app.services.chat_service import ChatService
from app.services.llm_service import LLMService
from app.utils.redis_cache import RedisObjectCache
from app.services.knowledge_base.cache_invalidation import QueryCache


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_ENABLED", False)
    monkeypatch.setattr(settings, "USER_MEMORY_ENABLED", False)
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", False)


class Cache:
    def __init__(self):
        self.values = {}

    async def aget(self, key):
        return deepcopy(self.values.get(key))

    async def aset(self, key, value, **kwargs):
        self.values[key] = deepcopy(value)


class Validator:
    def __init__(self, passes=(True,)):
        self.passes = list(passes)
        self.responses = []

    def validate(self, response, **kwargs):
        self.responses.append(response)
        result = ValidationResult()
        result.passed = self.passes[min(len(self.responses) - 1, len(self.passes) - 1)]
        result.needs_regeneration = not result.passed
        return result


def allowed_judge():
    """仅用于流程契约的显式语义替身，不代表真实模型识别质量。"""
    return SimpleNamespace(DEFAULT_THRESHOLD=.45, judge=AsyncMock(return_value={
        "grounded": True, "fact_score": 1.0, "has_unsupported_claim": False, "refuse_recommended": False}))


def graph_for_response(passes=(True,), refuse=False):
    graph = object.__new__(AgentGraph)
    graph._answer_cache = Cache()
    graph.validator = Validator(passes)
    graph.llm_judge = SimpleNamespace(DEFAULT_THRESHOLD=0.45, judge=AsyncMock(return_value={
        "grounded": not refuse, "fact_score": 0.1 if refuse else 1.0,
        "refuse_recommended": refuse, "has_unsupported_claim": refuse,
    }))
    graph._generate_response_with_llm = AsyncMock(return_value="请保持商品完好，退货运费按照政策中的责任划分承担。")
    return graph


def rag_state(user_id=1, context="耳机退货条件", intent="refund"):
    state = AgentState(session_id="s1", user_id=user_id, user_message="那运费呢？", detected_intent=intent)
    state.messages = [{"role": "user", "content": context}, {"role": "user", "content": state.user_message}]
    state.rewritten_query = f"{context}；退货运费"
    state.collected_info = {"rag_grounded": True, "kb_results": [
        {"id": "c1", "title": "退货政策", "content": "商品完好可退，运费按责任划分。", "version": 1},
    ]}
    return state


async def test_hydration_drops_turn_data_and_does_not_alias_saved_state(monkeypatch):
    saved = rag_state()
    saved.collected_info.update({"order_id": "ORD123456", "validation": {"passed": True},
        "confirm_refund": True, "user_memory": {"tags": ["old"]}, "graph_interrupt": ["old"],
        "inline_self_check": {"grounded": True}, "answer_from_cache": True})
    manager = SimpleNamespace(get_state=AsyncMock(return_value=saved), get_summary=AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.chat_service.session_manager", manager)
    state = await ChatService().hydrate_state("s1", 1, "会员有什么优惠？")
    assert state.collected_info == {"order_id": "ORD123456", "user_id": 1, "session_id": "s1"}
    assert len(saved.messages) == 2
    state.messages[0]["content"] = "changed"
    assert saved.messages[0]["content"] != "changed"
    assert not state.tool_calls and not state.trace and not state.reply


async def test_hydration_keeps_only_unfinished_action_parameters(monkeypatch):
    saved = rag_state()
    saved.pending_confirmations = ["apply_refund"]
    saved.collected_info.update({"refund_preview": {"order_id": "ORD123456"}, "reason": "破损", "confirm_refund": True})
    monkeypatch.setattr("app.services.chat_service.session_manager", SimpleNamespace(
        get_state=AsyncMock(return_value=saved), get_summary=AsyncMock(return_value=None)))
    state = await ChatService().hydrate_state("s1", 1, "确认")
    assert state.pending_confirmations == ["apply_refund"]
    assert state.collected_info["refund_preview"] == {"order_id": "ORD123456"}
    assert "confirm_refund" not in state.collected_info
    state.collected_info["refund_preview"]["order_id"] = "other"
    assert saved.collected_info["refund_preview"]["order_id"] == "ORD123456"


async def test_local_state_survives_short_cache_ttl_and_is_copied(monkeypatch):
    manager = SessionManager()
    manager._redis_available = False
    saved = rag_state()
    await manager.save_state(saved)
    manager._state_cache["s1"] = (manager._state_cache["s1"][0], __import__("time").time() - 20)
    state = await manager.get_state("s1")
    state.messages.clear()
    assert (await manager.get_state("s1")).messages
    restored = manager._deserialize_state(manager._serialize_state(AgentState(
        session_id="s2", user_id=1, user_message="x", missing_slots=["reason"], needs_clarification=True)))
    assert restored.missing_slots == ["reason"] and restored.needs_clarification


async def test_cache_contains_only_final_validated_reply_and_hits_same_context():
    graph = graph_for_response()
    state = rag_state()
    assert await graph._node_response_generation(state) == AgentNode.END
    entry = next(iter(graph._answer_cache.values.values()))
    assert entry["validated"] and entry["reply"] == state.reply
    assert "参考来源" in entry["reply"]
    other = rag_state()
    await graph._node_response_generation(other)
    assert other.reply == state.reply and other.collected_info["answer_from_cache"]
    assert graph._generate_response_with_llm.await_count == 1


@pytest.mark.parametrize("change", ["user", "history", "evidence", "version", "profile", "prompt_version", "temperature", "output_limit"])
def test_cache_key_separates_context_identity_and_knowledge(change, monkeypatch):
    graph = graph_for_response()
    state = rag_state()
    original = graph._build_answer_cache_key(state)
    if change == "user":
        state.user_id = 2
    elif change == "history":
        state.messages[0]["content"] = "显示器保修"
    elif change == "evidence":
        state.collected_info["kb_results"][0]["content"] = "新版运费政策"
    elif change == "version":
        state.collected_info["kb_results"][0]["version"] = 2
    elif change == "temperature":
        monkeypatch.setattr(settings, "LLM_GENERATION_TEMPERATURE", 0.3)
    elif change == "output_limit":
        monkeypatch.setattr(settings, "LLM_MAX_TOKENS", settings.LLM_MAX_TOKENS + 1)
    elif change == "profile":
        state.collected_info["user_memory"] = {"memory_source": "db", "user_level": "VIP"}
    else:
        monkeypatch.setattr(settings, "LLM_PROMPT_VERSION", "new-test-version")
    assert graph._build_answer_cache_key(state) != original


async def test_old_unvalidated_cache_entry_is_ignored():
    graph = graph_for_response()
    key = graph._build_answer_cache_key(rag_state())
    graph._answer_cache.values[key] = "unvalidated legacy answer"
    assert await graph._answer_cache_get(key) is None


@pytest.mark.parametrize("intent", ["refund", "complaint", "technical", "general"])
async def test_refused_policy_reply_never_cached_or_streamed_as_draft(intent):
    graph = graph_for_response(refuse=True)
    state = rag_state(intent=intent)
    queue = asyncio.Queue()
    token = _stream_event_queue.set(queue)
    try:
        await graph._node_response_generation(state)
    finally:
        _stream_event_queue.reset(token)
    assert state.collected_info["rag_refused"]
    assert not state.collected_info["validation"]["passed"]
    assert not graph._answer_cache.values
    published = "".join(queue.get_nowait()[1] for _ in range(queue.qsize()))
    assert published == state.reply and "足够可靠" in published
    assert "运费按照" not in published


async def test_regeneration_reviews_replacement_after_deterministic_failure():
    graph = graph_for_response(passes=(False, True))
    state = rag_state()
    await graph._node_response_generation(state)
    assert len(graph.validator.responses) == 2
    assert graph.llm_judge.judge.await_count == 1  # 确定性失败先拦截，不浪费模型审查调用。
    assert "参考来源" not in graph.validator.responses[0]
    assert graph._answer_cache.values


async def test_failed_draft_terminates_without_handoff_or_cache():
    graph = graph_for_response(passes=(False, False))
    state = rag_state()
    assert await graph._node_response_generation(state) == AgentNode.END
    assert not graph._answer_cache.values
    assert len(graph.validator.responses) == 2
    assert "参考来源" not in state.reply and not state.need_human and not state.tool_calls


async def test_no_tokens_are_published_while_judge_is_pending():
    graph = graph_for_response()
    entered, release = asyncio.Event(), asyncio.Event()
    async def judge(**kwargs):
        entered.set()
        await release.wait()
        return {"grounded": True, "fact_score": 1, "refuse_recommended": False}
    graph.llm_judge.judge = judge
    state = rag_state()
    queue = asyncio.Queue()
    token = _stream_event_queue.set(queue)
    try:
        task = asyncio.create_task(graph._node_response_generation(state))
        await entered.wait()
        assert queue.empty() and not graph._answer_cache.values
        release.set()
        await task
    finally:
        _stream_event_queue.reset(token)
    assert not queue.empty()


async def test_local_concurrent_turn_reads_preceding_saved_state():
    manager = SessionManager()
    manager._redis_available = False
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []
    async def first():
        async with manager.session_lock("s1"):
            entered.set()
            await release.wait()
            await manager.save_state(rag_state())
    async def second():
        await entered.wait()
        async with manager.session_lock("s1"):
            seen.append(await manager.get_state("s1"))
    one, two = asyncio.create_task(first()), asyncio.create_task(second())
    await entered.wait()
    await asyncio.sleep(0)
    assert not seen
    release.set()
    await asyncio.gather(one, two)
    assert seen[0].messages and not manager._turn_locks


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.fail_renew = False
    async def ping(self):
        return True
    async def get(self, key):
        return self.values.get(key)
    async def set(self, key, value, **kwargs):
        if kwargs.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True
    async def eval(self, script, count, key, token, *args):
        if self.values.get(key) != token or (args and self.fail_renew):
            return 0
        if not args:
            self.values.pop(key)
        return 1


async def test_shared_state_ignores_stale_local_copy(monkeypatch):
    import json
    manager, redis = SessionManager(), FakeRedis()
    manager._redis_available = True
    manager._state_cache["s1"] = (rag_state(), __import__("time").time())
    state = rag_state(context="new from another worker")
    redis.values["session:s1"] = json.dumps(manager._serialize_state(state))
    monkeypatch.setattr("app.agent.memory.get_redis", AsyncMock(return_value=redis))
    assert (await manager.get_state("s1")).messages[0]["content"] == "new from another worker"
    redis.values.clear()
    assert await manager.get_state("s1") is None


async def test_busy_shared_lock_is_explicit_and_does_not_delete_other_owner(monkeypatch):
    manager, redis = SessionManager(), FakeRedis()
    manager._redis_available = True
    redis.values["session_lock:s1"] = "other-owner"
    monkeypatch.setattr("app.agent.memory.get_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr("app.agent.memory.LOCK_MAX_RETRIES", 1)
    monkeypatch.setattr("app.agent.memory.LOCK_RETRY_INTERVAL", 0.01)
    with pytest.raises(SessionBusyError):
        async with manager.session_lock("s1"):
            pytest.fail("busy turn executed")
    assert redis.values["session_lock:s1"] == "other-owner"
    assert not manager._turn_locks


async def test_expired_lease_cannot_delete_new_owner(monkeypatch):
    manager, redis = SessionManager(), FakeRedis()
    manager._redis_available = True
    monkeypatch.setattr("app.agent.memory.get_redis", AsyncMock(return_value=redis))
    async with manager.session_lock("s1"):
        assert redis.values["session_lock:s1"] != "1"
        redis.values["session_lock:s1"] = "replacement"
    assert redis.values["session_lock:s1"] == "replacement"


async def test_lease_loss_cancels_turn_before_it_saves(monkeypatch):
    manager, redis = SessionManager(), FakeRedis()
    manager._redis_available = True
    redis.fail_renew = True
    monkeypatch.setattr("app.agent.memory.get_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr("app.agent.memory.LOCK_TIMEOUT_SECONDS", 0.03)
    saved = False
    with pytest.raises(SessionLeaseError):
        async with manager.session_lock("s1"):
            await asyncio.sleep(0.2)
            saved = True
    assert not saved and not manager._turn_locks


@pytest.mark.parametrize("cache_class", [RedisObjectCache, QueryCache])
async def test_shared_cache_miss_does_not_resurrect_invalidated_local_value(cache_class, monkeypatch):
    redis = FakeRedis()
    if cache_class is RedisObjectCache:
        cache = cache_class("test:")
        cache._local_set("key", "stale")
        monkeypatch.setattr(cache, "_get_redis", AsyncMock(return_value=redis))
    else:
        cache = cache_class()
        cache.set("key", "stale")
        monkeypatch.setattr(cache, "_async_redis", AsyncMock(return_value=redis))
    assert await cache.aget("key") is None


async def test_llm_metadata_is_task_local_and_returned_through_runnable():
    service = LLMService()
    async def call(user_id, **kwargs):
        service.last_call_metadata = {"user_id": user_id, "tokens": user_id}
        await asyncio.sleep(0)
        service.last_call_metadata["model"] = str(user_id)
        return str(user_id), "general", user_id
    service.chat = call
    graph = object.__new__(AgentGraph)
    graph.llm_service = service
    runnable = RunnableLambda(graph._invoke_llm_service)
    results = await asyncio.gather(*(runnable.ainvoke({"user_id": i}) for i in (1, 2)))
    assert [(result[0], result[3]["user_id"], result[3]["model"]) for result in results] == [("1", 1, "1"), ("2", 2, "2")]
    await service.close()


async def test_old_confirmation_in_history_does_not_authorize_new_refund():
    graph = object.__new__(AgentGraph)
    state = AgentState(session_id="s1", user_id=1, user_message="给另一个订单退款")
    state.messages = [{"role": "user", "content": "确认"}]
    assert not graph._refund_confirmed(state)


@pytest.mark.parametrize("streaming", [False, True])
async def test_actual_graph_refund_preview_then_clean_confirmation(streaming, monkeypatch):
    from app.agent.tools import ToolExecutionResult
    graph = AgentGraph()
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    graph.validator = Validator()
    manager = SessionManager()
    manager._redis_available = False
    monkeypatch.setattr("app.services.chat_service.session_manager", manager)
    calls = []
    async def invoke(name, **args):
        calls.append((name, args))
        return ToolExecutionResult(success=True, data={"refund_id": "R1", "amount": "99.00",
            "status": "pending_review", "currency": "CNY", "is_simulated": True})
    monkeypatch.setattr("app.agent.nodes.tool_node.invoke_tool", invoke)
    service = ChatService(graph)
    state = await service.hydrate_state("refund-test", 1, "ORD123456 我要退款，商品包装严重破损")
    async def execute(state):
        if streaming:
            events = [event async for event in service.process_stream(state)]
            assert events[-1]["type"] == "done"
            return state
        return await service.process(state)
    state = await execute(state)
    assert not calls and state.pending_confirmations == ["apply_refund"]
    assert "是否确认" in state.reply
    await manager.save_state(state)
    state = await service.hydrate_state("refund-test", 1, "确认")
    state = await execute(state)
    assert len(calls) == 1 and calls[0][0] == "apply_refund"
    assert not state.pending_confirmations and "refund_preview" not in state.collected_info
    await manager.save_state(state)
    state = await service.hydrate_state("refund-test", 1, "你好")
    state = await execute(state)
    assert len(calls) == 1 and state.collected_info.get("smalltalk")
    await graph.llm_service.close()


@pytest.fixture
def chat_api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.models.models import Base, User, Order
    from app.security import get_current_user, create_access_token
    from app.utils.database import get_db
    import app.routers.chat as routes

    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'chat.db').as_posix()}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user = User(id=1, username="turn-regression", role="user", token_version=0)
    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(user)
            db.add(Order(order_id="ORD123456", user_id=1, status="已发货", status_code="shipped", total_amount=99, items=[]))
            await db.commit()
    asyncio.run(setup())
    async def db_override():
        async with factory() as db:
            yield db
    async def auth_override():
        return user
    manager = SessionManager()
    manager._redis_available = False
    graph = AgentGraph()
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    graph.validator = Validator()
    monkeypatch.setattr(routes, "session_manager", manager)
    monkeypatch.setattr("app.services.chat_service.session_manager", manager)
    monkeypatch.setattr(routes.chat_service, "agent_graph", graph)
    monkeypatch.setattr("app.utils.database.async_session", factory)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = auth_override
    with TestClient(app) as client:
        yield client, manager, factory, create_access_token(user)
    asyncio.run(graph.llm_service.close())
    asyncio.run(engine.dispose())


def test_rest_and_websocket_share_clean_refund_turns_and_persistence(chat_api, monkeypatch):
    from sqlalchemy import select
    from app.models.models import Message, AgentTrace, EvaluationResult, Session
    from app.agent.tools import ToolExecutionResult
    client, manager, factory, token = chat_api
    calls = []
    async def invoke(name, **args):
        calls.append((name, args))
        return ToolExecutionResult(success=True, data={"refund_id": "R1", "amount": "99.00",
            "status": "pending_review", "currency": "CNY", "is_simulated": True})
    monkeypatch.setattr("app.agent.nodes.tool_node.invoke_tool", invoke)
    assert client.get("/docs").status_code == 200
    result = client.post("/api/v1/chat/send", json={
        "user_id": 1, "message": "ORD123456 我要退款，商品包装严重破损",
    })
    assert result.status_code == 200, result.text
    session_id = result.json()["session_id"]
    assert "是否确认" in result.json()["reply"] and not calls
    with client.websocket_connect(f"/api/v1/chat/stream?access_token={token}") as ws:
        ws.send_json({"user_id": 1, "session_id": session_id, "message": "确认"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] in ("stream_end", "error"):
                break
        assert events[-1]["type"] == "stream_end", events
        assert len(calls) == 1
        final = next(event["reply"] for event in events if event["type"] == "done")
        assert "R1" in final
        assert "".join(event["content"] for event in events if event["type"] == "token") == final
    async def inspect():
        async with factory() as db:
            session = await db.get(Session, session_id)
            messages = (await db.execute(select(Message).where(Message.session_id == session_id))).scalars().all()
            traces = (await db.execute(select(AgentTrace))).scalars().all()
            evaluations = (await db.execute(select(EvaluationResult))).scalars().all()
            return session.message_count, messages, traces, evaluations
    count, messages, traces, evaluations = asyncio.run(inspect())
    assert count == 4 and len(messages) == 4 and traces and len(evaluations) == 2
    assert messages[-1].content == final
    assert not manager._turn_locks


def test_api_busy_rejection_never_writes_message(chat_api, monkeypatch):
    from contextlib import asynccontextmanager
    from sqlalchemy import select
    from app.models.models import Message
    client, manager, factory, token = chat_api
    session_id = client.post("/api/v1/chat/sessions?user_id=1").json()["data"]["id"]
    @asynccontextmanager
    async def busy(session_id):
        raise SessionBusyError("busy-test")
        yield
    monkeypatch.setattr(manager, "session_lock", busy)
    result = client.post("/api/v1/chat/send", json={"user_id": 1, "session_id": session_id, "message": "你好"})
    assert result.status_code == 409
    with client.websocket_connect(f"/api/v1/chat/stream?access_token={token}") as ws:
        ws.send_json({"user_id": 1, "session_id": session_id, "message": "你好"})
        assert ws.receive_json()["code"] == 409
    async def inspect():
        async with factory() as db:
            return (await db.execute(select(Message))).scalars().all()
    assert not asyncio.run(inspect())


async def test_streamed_rag_graph_exposes_only_final_refusal(monkeypatch):
    graph = AgentGraph()
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    graph.validator = Validator()
    graph.llm_judge = SimpleNamespace(DEFAULT_THRESHOLD=0.45, judge=AsyncMock(return_value={
        "grounded": False, "fact_score": 0, "refuse_recommended": True,
    }))
    class KB:
        async def async_search(self, **kwargs):
            return {"success": True, "results": [{"id": "c1", "title": "政策", "content": "商品完好可退", "final_score": 0.9}]}
    monkeypatch.setattr("app.agent.graph.get_knowledge_base", lambda: KB())
    async def stream(**kwargs):
        yield "未经支持的运费承诺"
    monkeypatch.setattr(graph.llm_service, "stream_chat", stream)
    state = AgentState(session_id="rag-stream", user_id=1, user_message="退货政策是什么？")
    events = [event async for event in graph.run_stream(state)]
    assert events[-1]["type"] == "done"
    text = "".join(event["content"] for event in events if event["type"] == "token")
    assert text == events[-1]["reply"] and "足够可靠" in text
    assert "未经支持" not in text
    await graph.llm_service.close()


async def test_interrupt_snapshot_remains_json_serializable():
    import json
    graph = AgentGraph()
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    state = AgentState(session_id="json-preview", user_id=1, user_message="ORD123456 我要退款，商品包装严重破损")
    state = await graph.run(state)
    snapshot = SessionManager()._serialize_state(state)
    json.dumps(snapshot)
    assert "graph_interrupt" not in state.collected_info
    assert state.pending_confirmations == ["apply_refund"]
    await graph.llm_service.close()


async def test_cancelled_waiter_does_not_unlock_active_turn():
    manager = SessionManager()
    manager._redis_available = False
    async with manager.session_lock("s1"):
        async def wait():
            async with manager.session_lock("s1"):
                pytest.fail("cancelled waiter acquired lock")
        waiter = asyncio.create_task(wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert manager._turn_locks["s1"]["lock"].locked()
    assert not manager._turn_locks


def test_api_concurrent_requests_keep_transcript_and_counts(chat_api, monkeypatch):
    import httpx
    from contextlib import asynccontextmanager
    from sqlalchemy import select
    from app.models.models import Message, Session
    import app.routers.chat as routes
    client, manager, factory, token = chat_api
    session_id = client.post("/api/v1/chat/sessions?user_id=1").json()["data"]["id"]
    original_process = routes.chat_service.process
    original_lock = manager.session_lock
    async def run():
        entered, release, second_attempt = asyncio.Event(), asyncio.Event(), asyncio.Event()
        attempts = 0
        observed = []
        @asynccontextmanager
        async def tracked_lock(session_id):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                second_attempt.set()
            async with original_lock(session_id) as assert_owner:
                yield assert_owner
        async def process(state):
            observed.append(len(state.messages))
            if len(observed) == 1:
                entered.set()
                await release.wait()
            return await original_process(state)
        monkeypatch.setattr(manager, "session_lock", tracked_lock)
        monkeypatch.setattr(routes.chat_service, "process", process)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url="http://test") as http:
            first = asyncio.create_task(http.post("/api/v1/chat/send", json={"user_id": 1, "session_id": session_id, "message": "你好"}))
            await entered.wait()
            second = asyncio.create_task(http.post("/api/v1/chat/send", json={"user_id": 1, "session_id": session_id, "message": "谢谢"}))
            await second_attempt.wait()
            assert observed == [1]
            release.set()
            responses = await asyncio.gather(first, second)
        assert all(response.status_code == 200 for response in responses)
        assert observed == [1, 3]
        async with factory() as db:
            session = await db.get(Session, session_id)
            messages = (await db.execute(select(Message).where(Message.session_id == session_id).order_by(Message.id))).scalars().all()
            assert session.message_count == 4
            assert [m.role.value for m in messages] == ["user", "assistant", "user", "assistant"]
    client.portal.call(run)


async def test_ownership_is_checked_before_persistence_even_before_heartbeat(monkeypatch):
    manager, redis = SessionManager(), FakeRedis()
    manager._redis_available = True
    monkeypatch.setattr("app.agent.memory.get_redis", AsyncMock(return_value=redis))
    async with manager.session_lock("s1") as assert_owner:
        await assert_owner()
        redis.values["session_lock:s1"] = "new-owner"
        with pytest.raises(SessionLeaseError):
            await assert_owner()
    assert redis.values["session_lock:s1"] == "new-owner"


async def test_model_supplied_references_are_replaced_with_actual_evidence():
    graph = graph_for_response()
    graph._generate_response_with_llm = AsyncMock(return_value="商品完好可退。\n参考来源：\n虚构来源")
    state = rag_state()
    await graph._node_response_generation(state)
    assert "虚构来源" not in state.reply and "退货政策" in state.reply
    assert "参考来源" not in graph.validator.responses[0]


async def test_missing_handoff_tool_is_terminal_instead_of_looping(monkeypatch):
    graph = graph_for_response()
    state = rag_state()
    state.user_message = "转人工"
    state.need_human = True
    monkeypatch.setattr("app.agent.nodes.handoff_node.invoke_tool", AsyncMock(return_value=None))
    assert await graph._node_human_handoff(state) == AgentNode.RESPONSE_GENERATION
    assert await graph._node_response_generation(state) == AgentNode.END
    assert "人工" in state.reply
