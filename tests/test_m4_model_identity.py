"""真实隔离SQL账本，模型适配器替身；主图/接口/兼容摘要入口真实运行。"""

from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from langchain_core.messages import AIMessage
from app.agent.graph import AgentGraph
from app.agent.state import AgentState, AgentNode
from app.agent.memory import SessionManager
from app.config.config import settings
from app.models.models import ModelCallRecord, User
from app.services.model_gateway import model_context, CALL_CONTEXT, inherited_model_context
from app.services.llm_service import LLMService, ModelConfig, IntentClassification, LLMResponse
from tests.test_m4_gateway import factory as factory


def fake_service(monkeypatch, factory):
    monkeypatch.setattr(settings, "LLM_DAILY_USER_TOKEN_BUDGET", 100000)
    monkeypatch.setattr(settings, "LLM_MAX_TOKENS", 256)
    monkeypatch.setattr("app.services.llm_service.settings.LLM_MAX_TOKENS", 256)
    monkeypatch.setattr("app.utils.database.async_session", factory)
    service = LLMService()
    service.models = [ModelConfig("synthetic", api_key="synthetic-key", model="synthetic")]
    service.local_model = None
    service.RETRY_DELAY = 0

    class Chat:
        def with_structured_output(self, contract, **kwargs):
            value = (contract(reply="合成有效回复", intent="technical") if contract is LLMResponse
                     else contract(summary_text="合成摘要仅描述历史背景，不授权任何业务动作。")
                     if "summary_text" in contract.model_fields else contract(intent="technical"))

            class Structured:
                async def ainvoke(self, messages):
                    return {"raw": AIMessage(content="", usage_metadata={
                        "input_tokens": 2, "output_tokens": 1, "total_tokens": 3}),
                            "parsed": value, "parsing_error": None}

            return Structured()

    monkeypatch.setattr(service, "_get_chat_model", lambda *a, **kw: Chat())
    return service


@pytest.mark.parametrize("streaming", [False, True])
async def test_direct_graph_all_purposes_inherit_real_user_and_one_unique_scope(factory, monkeypatch, streaming):
    from langgraph.checkpoint.memory import InMemorySaver

    service = fake_service(monkeypatch, factory)
    graph = AgentGraph()
    graph.llm_service = service
    graph.checkpointer = InMemorySaver()

    async def all_purposes(state):
        for purpose in ["intent", "query_rewrite", "judge", "conversation_summary"]:
            assert await service.run_structured("返回JSON", "合成文本", IntentClassification, tags=[purpose])
        assert await service._call_model_api(service.models[0], state.user_id, state.session_id, "合成回答")
        state.reply = "合成最终结果"
        return AgentNode.END

    graph.nodes[AgentNode.START] = all_purposes
    graph.langgraph = graph._build_langgraph()
    try:
        for _ in range(2):
            state = AgentState(session_id="same-session", user_id=101, user_message="合成问题")
            if streaming:
                async for _event in graph.run_stream(state):
                    pass
            else:
                await graph.run(state)
            assert CALL_CONTEXT.get() is None
        async with factory() as db:
            rows = (await db.scalars(select(ModelCallRecord))).all()
        assert len(rows) == 10 and {r.user_id for r in rows} == {101}
        assert {r.purpose for r in rows} == {"intent", "query_rewrite", "judge", "conversation_summary", "generation"}
        assert len({r.run_id for r in rows}) == 2
        for run in {r.run_id for r in rows}:
            assert len([r for r in rows if r.run_id == run]) == 5
    finally:
        await service.close()


async def test_nested_scope_preserves_run_and_factory_and_rejects_other_user(factory):
    async with model_context(101, "real-parent-run", factory=factory):
        original = CALL_CONTEXT.get()
        async with inherited_model_context(101, "ignored-child"):
            assert CALL_CONTEXT.get() is original
        with pytest.raises(RuntimeError, match="user_mismatch"):
            async with inherited_model_context(202, "foreign"):
                pytest.fail("foreign identity must not enter")
        assert CALL_CONTEXT.get() is original
    assert CALL_CONTEXT.get() is None


async def test_unscoped_structured_call_fails_closed_without_system_user_ledger(factory, monkeypatch):
    service = fake_service(monkeypatch, factory)
    try:
        assert await service.run_structured("JSON", "合成文本", IntentClassification) is None
        async with factory() as db:
            assert (await db.scalars(select(ModelCallRecord))).all() == []
    finally:
        await service.close()


async def test_standalone_chat_retries_share_one_scope_and_identity(factory, monkeypatch):
    service = fake_service(monkeypatch, factory)
    service.models = [ModelConfig(f"synthetic-{i}", api_key="synthetic-key", model="synthetic") for i in range(8)]
    seen = []

    class Chat:
        def with_structured_output(self, *args, **kwargs):
            return self

        async def ainvoke(self, messages):
            seen.append(dict(CALL_CONTEXT.get()))
            raise RuntimeError("synthetic failure")

    monkeypatch.setattr(service, "_get_chat_model", lambda *args, **kwargs: Chat())
    try:
        await service.chat(101, "stable-session", "合成问题")
        assert len(seen) == 6 and len({s["run_id"] for s in seen}) == 1
        assert {s["user_id"] for s in seen} == {101}
        async with factory() as db:
            rows = (await db.scalars(select(ModelCallRecord))).all()
            assert len(rows) == 6 and len({r.run_id for r in rows}) == 1
        assert CALL_CONTEXT.get() is None
    finally:
        await service.close()


async def test_direct_chat_does_not_change_parent_user_scope(factory, monkeypatch):
    service = fake_service(monkeypatch, factory)
    try:
        async with model_context(101, "trusted-parent", factory=factory):
            with pytest.raises(RuntimeError, match="user_mismatch"):
                await service.chat(202, "foreign-session", "合成问题")
            stream = service.stream_chat(202, "foreign-session", "合成问题")
            with pytest.raises(RuntimeError, match="user_mismatch"):
                await stream.__anext__()
            assert CALL_CONTEXT.get()["run_id"] == "trusted-parent"
        async with factory() as db:
            assert (await db.scalars(select(ModelCallRecord))).all() == []
    finally:
        await service.close()


async def test_standalone_stream_releases_scope_and_uses_new_run_each_request(factory, monkeypatch):
    from langchain_core.messages import AIMessageChunk
    service = fake_service(monkeypatch, factory)
    closed = []

    class Stream:
        async def astream(self, *args, **kwargs):
            try:
                yield AIMessageChunk(content="合成流回复", usage_metadata={
                    "input_tokens": 2, "output_tokens": 1, "total_tokens": 3})
            finally:
                closed.append(True)

    monkeypatch.setattr(service, "_get_chat_model", lambda *a, **kw: Stream())
    try:
        for _ in range(2):
            assert "".join([part async for part in service.stream_chat(101, "same-session", "合成问题")]) == "合成流回复"
            assert CALL_CONTEXT.get() is None
        async with factory() as db:
            rows = (await db.scalars(select(ModelCallRecord))).all()
        assert len(rows) == 2 and len({r.run_id for r in rows}) == 2
        assert {r.user_id for r in rows} == {101}
        assert {r.purpose for r in rows} == {"generation_stream"}
        assert closed == [True, True]
    finally:
        await service.close()


async def test_legacy_summary_does_not_dispatch_another_model(factory, monkeypatch):
    service = fake_service(monkeypatch, factory)
    monkeypatch.setattr("app.services.llm_service.LLMService", lambda: service)
    manager = SessionManager()
    state = AgentState(session_id="summary-session", user_id=101, user_message="合成问题",
                       messages=[{"role": "user", "content": "合成历史问题"}] * 22)
    try:
        assert await manager._compress_context(state)
        async with factory() as db:
            row = await db.scalar(select(ModelCallRecord))
            assert row is None
        from app.services.conversation_summary import parse_summary
        assert parse_summary(state.conversation_summary) is not None
        assert all(message["role"] != "system" for message in state.messages)
        assert CALL_CONTEXT.get() is None
    finally:
        await service.close()


@pytest.mark.parametrize("failure", [False, True])
async def test_rag_api_binds_authenticated_user_and_closes_client(factory, monkeypatch, failure):
    import httpx
    from fastapi import FastAPI
    from app.routers import knowledge
    from app.security import require_user

    captured = []

    class KB:
        async def async_search(self, **kwargs):
            return {"results": [{"id": "synthetic-chunk", "title": "合成政策", "content": "合成内容"}]}

        def evidence_is_current(self, items):
            return True

    class Client:
        async def chat(self, **kwargs):
            captured.append((kwargs, dict(CALL_CONTEXT.get() or {})))
            if failure:
                raise RuntimeError("synthetic-private-error")
            return "合成答案", "technical", 3

        close = AsyncMock()

    client = Client()
    monkeypatch.setattr(knowledge, "get_knowledge_base", lambda: KB())
    monkeypatch.setattr(knowledge, "LLMService", lambda: client)
    app = FastAPI()
    app.include_router(knowledge.router)
    app.dependency_overrides[require_user] = lambda: User(id=101, username="synthetic", role="user")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as api:
        response = await api.post("/api/v1/knowledge/rag-query", json={"query": "合成问题", "use_llm": True})
    assert response.status_code == 200
    assert captured[0][0]["user_id"] == 101 and captured[0][1]["user_id"] == 101
    assert captured[0][1]["run_id"].startswith("rag-query:")
    client.close.assert_awaited_once()
    assert CALL_CONTEXT.get() is None and "synthetic-private-error" not in response.text
