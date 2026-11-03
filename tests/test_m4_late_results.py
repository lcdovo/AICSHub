"""模拟依赖吞掉取消后迟到返回；不能据此提交状态或发布文本。"""

import asyncio
from types import SimpleNamespace
import pytest
from sqlalchemy import select, func
from app.agent.memory import SessionManager
from app.models.models import ModelCallRecord, User, ChatRun, RunEvent, Message, MessageRole, Ticket
from app.services.model_gateway import ModelGateway, model_context
from app.services.llm_service import LLMService, ModelConfig
from app.services.chat_service import ChatService
from app.services import run_execution as runs
from app.schemas.schemas import ChatRequest
from tests.test_m4_gateway import factory as factory
from tests.test_ticket_idempotency import api_context


async def suppress_timeout():
    try:
        await asyncio.sleep(.2)
    except asyncio.CancelledError:
        # 某些线程桥接/第三方客户端在结束底层工作后才返回，并不传播取消。
        await asyncio.sleep(.01)


@pytest.mark.parametrize("blocking", [False, True, "thread"])
async def test_late_nonstream_provider_result_is_rejected_but_known_usage_is_counted(factory, blocking):
    from langchain_core.messages import AIMessage

    class Late:
        async def ainvoke(self, messages):
            if blocking == "thread":
                worker = asyncio.create_task(asyncio.to_thread(__import__("time").sleep, .04))
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    await worker
                assert worker.done()
            elif blocking:
                __import__("time").sleep(.04)
            else:
                await suppress_timeout()
            return AIMessage(content="synthetic-late-draft",
                             usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3})

    gateway = ModelGateway()
    service = SimpleNamespace(_semaphore=asyncio.Semaphore(1), TIMEOUT=.02)
    async with model_context(101, "late-model", factory=factory):
        with pytest.raises(TimeoutError):
            await gateway.invoke(service, ModelConfig("synthetic", model="synthetic"),
                                 ["x"], Late(), "generation", output_limit=40)
    async with factory() as db:
        call = await db.scalar(select(ModelCallRecord))
        assert call.error_code == "TimeoutError" and call.status != "succeeded"
        assert call.input_tokens == 2 and call.output_tokens == 1


async def test_late_stream_chunk_is_never_yielded(factory, monkeypatch):
    from app.config.config import settings
    from langchain_core.messages import AIMessageChunk

    monkeypatch.setattr(settings, "LLM_MAX_TOKENS", 40)
    monkeypatch.setattr("app.services.llm_service.settings.LLM_MAX_TOKENS", 40)
    service = LLMService()
    service.TIMEOUT = .02
    closed = []

    class LateStream:
        async def astream(self, *args, **kwargs):
            try:
                await suppress_timeout()
                yield AIMessageChunk(content="synthetic-late-chunk")
            finally:
                closed.append(True)

    chunks = []
    try:
        async with model_context(101, "late-stream", factory=factory):
            with pytest.raises(TimeoutError):
                async for chunk in service._gateway_stream(
                    ModelConfig("synthetic", model="synthetic"), ["x"], LateStream(), 101, "late-stream"
                ):
                    chunks.append(chunk.content)
        assert chunks == []
        assert closed == [True]
        async with factory() as db:
            assert (await db.scalar(select(ModelCallRecord))).status != "succeeded"
    finally:
        await service.close()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("blocking", [False, True])
async def test_run_rejects_graph_returning_after_suppressed_timeout(tmp_path, monkeypatch, streaming, blocking):
    async with api_context(tmp_path / "late-run.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        monkeypatch.setattr(runs, "RUN_TIMEOUT_SECONDS", .02)
        manager = SessionManager()
        manager._redis_available = False
        monkeypatch.setattr("app.services.chat_service.session_manager", manager)
        calls = []

        async def process(state):
            calls.append(True)
            if blocking:
                __import__("time").sleep(.04)
            else:
                await suppress_timeout()
            state.reply = "synthetic-late-run-reply"
            return state

        async def stream(state):
            await process(state)
            yield {"type": "done", "reply": state.reply}

        service = ChatService(SimpleNamespace(run=process, run_stream=stream))
        async with factory() as db:
            run, _ = await runs.accept(db, await db.get(User, 101), ChatRequest(
                user_id=101, session_id="session-101", message="合成迟到响应", client_message_id="late-run"))
        await runs.execute(factory, run.id, service, manager, None, streaming)
        await runs.recover(factory, run.id)
        async with factory() as db:
            assert (await db.get(ChatRun, run.id)).status == "failed"
            assert await db.scalar(select(func.count()).select_from(Message).where(
                Message.role == MessageRole.ASSISTANT)) == 0
            events = (await db.scalars(select(RunEvent))).all()
            assert not any(e.event_type in {"token", "done", "stream_end"} for e in events)
        assert len(calls) == 1


async def test_late_result_after_business_commit_recovers_receipt_without_resubmit(tmp_path, monkeypatch):
    from app.agent.tools import CreateTicketTool
    from app.agent.ticket_authorization import authorize_ticket

    async with api_context(tmp_path / "late-receipt.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        monkeypatch.setattr(runs, "RUN_TIMEOUT_SECONDS", 1)
        manager = SessionManager()
        manager._redis_available = False
        monkeypatch.setattr("app.services.chat_service.session_manager", manager)
        receipts = []

        async def process(state):
            payload = dict(user_id=101, session_id=state.session_id, category="技术支持",
                           content="合成问题", priority="medium",
                           idempotency_key=f"chat-ticket:{state.session_id}:{state.source_message_id}")
            result = await CreateTicketTool().execute(**payload, authorization=authorize_ticket(
                payload, state.source_message_id, state.actor_id))
            assert result["success"]
            receipts.append(result["data"]["ticket_id"])
            try:
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                await asyncio.sleep(.01)
            state.reply = "synthetic-late-after-commit"
            return state

        async with factory() as db:
            run, _ = await runs.accept(db, await db.get(User, 101), ChatRequest(
                user_id=101, session_id="session-101", message="合成问题", client_message_id="late-receipt"))
        await runs.execute(factory, run.id, ChatService(SimpleNamespace(run=process)), manager, None, False)
        await runs.recover(factory, run.id)
        await runs.recover(factory, run.id)
        async with factory() as db:
            current = await db.get(ChatRun, run.id)
            assert current.status == "succeeded" and receipts[0] in current.result["reply"]
            assert "synthetic-late-after-commit" not in current.result["reply"]
            assert await db.scalar(select(func.count()).select_from(Ticket)) == 1
        assert len(receipts) == 1
