"""能力契约、上下文、无进展退出及中断对账；全部使用合成数据和隔离SQL。"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextvars import ContextVar

import pytest
from sqlalchemy import select, func

from app.agent import mcp_client
from app.agent.capabilities import CAPABILITIES, project_observation
from app.agent.tools import ToolExecutionResult, BaseTool
from app.agent.request_context import stage_context
from app.agent.task_context import remember_task_turn
from app.agent.task_contracts import NextStep
from app.agent.task_loop import allowed_actions, observe_task
from app.agent.state import AgentNode
from app.models.models import AgentCapabilityCall, AgentTask, AgentTaskStep, ChatRun, Session, RefundAction, Order
from app.agent.read_authorization import authorize_read
from app.services.capability_journal import CapabilityJournal, JOURNAL, TASK_STATE, recover_calls
from tests.test_agent_task_loop import request_state, executor, reader, read_and_search, local_config as local_config
from tests.test_ticket_idempotency import api_context


@pytest.mark.parametrize("data", [{}, {"order_id": 123}, {"order_id": "ORD999999"},
                                  {"order_id": "ORD960001", "status_code": []}])
async def test_malformed_or_foreign_order_output_cannot_be_success(monkeypatch, data):
    monkeypatch.setattr(mcp_client, "_ENABLED", False)
    monkeypatch.setattr(mcp_client.TOOL_REGISTRY["query_order"], "execute_with_retry",
                        AsyncMock(return_value=ToolExecutionResult(success=True, data=data)))
    result = await mcp_client.invoke_tool("query_order", order_id="ORD960001")
    assert not result.success and result.error_type == "invalid_output"
    assert result.outcome == "failed" and result.call_id and result.observed_at and result.transport == "direct"


def test_observation_projection_keeps_status_after_huge_private_fields():
    data = {"address": "private" * 2000, "order_id": "ORD960001", "status_code": "delivered",
            "status": "已签收", "total_amount": "0.10", "shipping": {"address": "private", "carrier": "合成物流"}}
    projected = project_observation("query_order", {"success": True, "data": data})
    assert projected["data"]["status_code"] == "delivered"
    assert "private" not in json.dumps(projected)


@pytest.mark.parametrize("name", ["create_ticket", "apply_refund", "send_notification"])
async def test_writes_never_inherit_retry_policy(name):
    class BrokenWrite(BaseTool):
        max_retries = 5
        async def execute(self, **kwargs):
            self.calls += 1
            raise OSError("synthetic")
    tool = BrokenWrite()
    tool.name, tool.calls = name, 0
    result = await tool.execute_with_retry()
    assert not result.success and tool.calls == 1


async def test_late_direct_result_is_rejected_and_unknown_write_is_not_replayed(monkeypatch):
    monkeypatch.setattr(mcp_client, "_ENABLED", False)
    monkeypatch.setitem(CAPABILITIES, "create_ticket", replace(CAPABILITIES["create_ticket"], timeout_seconds=.01))
    async def late(**kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return ToolExecutionResult(success=True, data={"ticket_id": "late"})
    dispatch = AsyncMock(side_effect=late)
    monkeypatch.setattr(mcp_client.TOOL_REGISTRY["create_ticket"], "execute_with_retry", dispatch)
    monkeypatch.setattr("app.agent.receipt_recovery.recover", AsyncMock(return_value=None))
    result = await mcp_client.invoke_tool("create_ticket")
    assert not result.success and result.outcome == "unknown" and dispatch.await_count == 1


@pytest.mark.parametrize("error_field", ["isError", "is_error"])
async def test_mcp_error_flag_cannot_claim_success_and_write_never_falls_back(monkeypatch, error_field):
    client = mcp_client._McpToolClient()
    client._ready = True
    client._session = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(**{error_field: True},
        content=[SimpleNamespace(type="text", text='{"success":true,"data":{"ticket_id":"forged"}}')])))
    monkeypatch.setattr(mcp_client, "_ENABLED", True)
    monkeypatch.setattr(mcp_client, "_get_client", lambda: client)
    monkeypatch.setattr("app.agent.receipt_recovery.recover", AsyncMock(return_value=None))
    direct = AsyncMock()
    monkeypatch.setattr(mcp_client.TOOL_REGISTRY["create_ticket"], "execute_with_retry", direct)
    result = await mcp_client.invoke_tool("create_ticket")
    assert not result.success and result.outcome == "unknown" and not client._ready
    direct.assert_not_awaited()


def test_protocol_result_does_not_coerce_truthy_strings():
    with pytest.raises(ValueError):
        ToolExecutionResult.model_validate({"success": "false", "data": {"ticket_id": "forged"}})


async def test_mcp_reconnect_has_one_owner_and_concurrent_initialization(monkeypatch):
    events, calls = [], []
    caller = ContextVar("probe_caller", default=None)
    session = SimpleNamespace(initialize=AsyncMock(), list_tools=AsyncMock(), call_tool=AsyncMock(
        return_value=SimpleNamespace(content=[SimpleNamespace(type="text", text='{"success":true,"data":{"order_id":"ORD960001"}}')])))
    @asynccontextmanager
    async def connect(server):
        assert caller.get() is None
        calls.append(server)
        if len(calls) == 1:
            raise OSError("synthetic startup failure")
        owner = asyncio.current_task()
        events.append("entered")
        try:
            yield session
        finally:
            assert asyncio.current_task() is owner
            events.append("closed")
    @asynccontextmanager
    async def open_session():
        async with connect("server") as connected:
            yield connected
    monkeypatch.setattr(mcp_client, "_inprocess_session", open_session)
    monkeypatch.setattr(mcp_client, "_TRANSPORT", "inprocess")
    client = mcp_client._McpToolClient()
    client.retry_base = 0.
    token = caller.set("first-private-run")
    try:
        assert not await client._ensure()
        assert all(await asyncio.gather(*[client._ensure() for _ in range(8)]))
        assert len(calls) == 2 and session.initialize.await_count == 1
    finally:
        caller.reset(token)
        await client.aclose()
    assert events == ["entered", "closed"]


async def test_protocol_timeout_reports_actual_attempted_transport(monkeypatch):
    async def stalled(*args):
        await asyncio.Event().wait()
    monkeypatch.setattr(mcp_client, "_ENABLED", True)
    monkeypatch.setattr(mcp_client, "_get_client", lambda: SimpleNamespace(call=stalled))
    monkeypatch.setitem(CAPABILITIES, "create_ticket", replace(CAPABILITIES["create_ticket"], timeout_seconds=.01))
    monkeypatch.setattr("app.agent.receipt_recovery.recover", AsyncMock(return_value=None))
    result = await mcp_client.invoke_tool("create_ticket")
    assert result.outcome == "unknown" and result.transport == "mcp"


async def test_mcp_startup_dependency_failure_preserves_direct_fallback(monkeypatch):
    import importlib
    monkeypatch.setattr(mcp_client, "_ENABLED", True)
    monkeypatch.setattr(mcp_client, "_TRANSPORT", "inprocess")
    def unavailable(name):
        raise ImportError("synthetic missing dependency")
    monkeypatch.setattr(importlib, "import_module", unavailable)
    assert not await mcp_client.warmup_mcp_client()


async def test_disabled_mcp_does_not_start_protocol_during_startup(monkeypatch):
    monkeypatch.setattr(mcp_client, "_ENABLED", False)
    client = SimpleNamespace(_ensure=AsyncMock())
    monkeypatch.setattr(mcp_client, "_get_client", lambda: client)
    assert not await mcp_client.warmup_mcp_client()
    client._ensure.assert_not_awaited()


def test_long_diagnostic_context_preserves_sourced_feedback_but_no_tool_facts():
    state, _ = request_state(kind="troubleshoot", topic="technical", request_type="inquiry", conditions=[],
        subrequests=[], parameters={}, entities={"order_id": None, "product": "耳机"}, message="耳机无法开机")
    for number in range(12):
        state.source_message_id, state.user_message = number + 100, f"第{number}次反馈，重启后仍然无法开机"
        remember_task_turn(state, "user_report", state.user_message)
        remember_task_turn(state, "suggested_step", "请检查充电线，操作是否成功尚未知。")
    previous = state.task
    fresh = state.for_next_turn(101, "继续，换线之后还是无法开机")
    fresh.task = previous.model_copy(update={"status": "waiting_input"})
    fresh.source_message_id = 200
    fresh.conversation_summary = "旧摘要不能授权退款"
    payload = stage_context(fresh)
    memory = payload["task_memory"]
    assert len(previous.notes) == 24 and memory["records"]
    assert any("第11次反馈" in row["content"] for row in memory["records"])
    assert not memory["trusted_as_current_evidence"] and not memory["grants_authorization"]
    assert all(row["message_id"] and row["recorded_at"] for row in memory["records"])
    assert sum(len(row["content"]) for row in memory["records"]) <= 1800
    assert not fresh.tool_calls and not fresh.current_evidence() and not fresh.has_current_refund_confirmation()
    fresh.user_message = "今天有什么优惠活动？"
    assert "task_memory" not in stage_context(fresh)


@pytest.mark.parametrize("message,blocked", [("试过了，还是不行", True), ("请再说一遍上一步", False),
                                          ("还没试，请再发一次", False)])
def test_exact_failed_diagnostic_step_is_not_repeated(message, blocked):
    from app.agent.task_context import repeated_diagnostic_issues
    state, _ = request_state(kind="troubleshoot", topic="technical", request_type="inquiry", conditions=[],
                             subrequests=[], parameters={})
    remember_task_turn(state, "suggested_step", "请检查充电线，然后告诉我指示灯是否亮起。")
    state.source_message_id, state.user_message = 200, message
    state.task_runtime.response_goal = "diagnostic_feedback"
    assert bool(repeated_diagnostic_issues(state, "请检查充电线，然后告诉我指示灯是否亮起。")) is blocked


async def test_finish_cannot_skip_requested_preview_or_current_policy(monkeypatch):
    reader(monkeypatch)
    state, _ = request_state()
    node, _ = executor()
    await read_and_search(state, node)
    assert "finish" not in allowed_actions(state)
    assert "prepare_refund" in allowed_actions(state)
    state.task_runtime.next_step = NextStep(action="finish")
    await node(state)
    assert state.task.status == "stopped" and not state.pending_confirmations


async def test_changed_search_query_without_new_evidence_stops(monkeypatch):
    state, _ = request_state(kind="troubleshoot", topic="technical", request_type="inquiry", conditions=[], subrequests=[], parameters={})
    node, _ = executor()
    await node(state)
    assert await observe_task(state) == AgentNode.TASK_DECISION
    state.task_runtime.next_step = NextStep(action="search_knowledge", knowledge_focus="refund_policy")
    await node(state)
    assert await observe_task(state) == AgentNode.RESPONSE_GENERATION
    assert state.task.stop_reason == "no_progress" and not state.need_human


async def owned_journal(factory):
    async with factory() as db:
        session = await db.get(Session, "session-101")
        session.lease_token, session.lease_expires_at = "owner", datetime.utcnow() + timedelta(seconds=45)
        run = ChatRun(id="run-reliability", session_id=session.id, user_id=101, actor_id=101,
            client_message_id="reliability", request_hash="hash", request_data={"message": "合成请求"},
            sequence=1, status="running", lease_token="owner", lease_expires_at=session.lease_expires_at)
        db.add(run)
        await db.commit()
        return CapabilityJournal(factory, run.id, "owner", session.version)


async def test_journal_commits_before_dispatch_and_survives_final_result_failure(tmp_path, monkeypatch):
    async with api_context(tmp_path / "journal.db") as (_, factory):
        journal = await owned_journal(factory)
        state, _ = request_state()
        state.run_id = journal.run_id
        token = TASK_STATE.set(state)
        try:
            call_id = await journal.begin("query_order", {"order_id": "ORD960001", "authorization": "private"}, "read")
            async with factory() as db:
                row = await db.get(AgentCapabilityCall, call_id)
                assert row.status == "running" and row.task_progress["goal"] == state.task.goal
                assert "private" not in json.dumps(row.task_progress)
            await journal.finish(call_id, "succeeded", {"order_id": "ORD960001", "status_code": "delivered"})
            with pytest.raises(ValueError, match="already_dispatched"):
                await journal.begin("query_order", {"order_id": "ORD960001", "authorization": "different"}, "read")
        finally:
            TASK_STATE.reset(token)
        async with factory() as db:
            run = await db.get(ChatRun, journal.run_id)
            recovered = await recover_calls(db, run)
            await db.commit()
            task = await db.get(AgentTask, state.task.task_id)
            assert task.status == "waiting_input" and not recovered["old_evidence_restored"]
            assert "status_code" not in json.dumps(task.progress) and not recovered["tools_replayed"]
        async with factory() as db:
            await recover_calls(db, await db.get(ChatRun, journal.run_id))
            await db.commit()
            assert await db.scalar(select(func.count()).select_from(AgentTaskStep)) == 1


@pytest.mark.parametrize("effect,expected", [("read", "waiting_input"), ("write", "stopped")])
async def test_interrupted_call_recovers_without_dispatch(tmp_path, effect, expected):
    async with api_context(tmp_path / "interrupted.db") as (_, factory):
        journal = await owned_journal(factory)
        state, _ = request_state()
        state.run_id = journal.run_id
        token = TASK_STATE.set(state)
        try:
            call_id = await journal.begin("prepare_refund" if effect == "write" else "query_order", {"order_id": "ORD960001"}, effect)
        finally:
            TASK_STATE.reset(token)
        async with factory() as db:
            result = await recover_calls(db, await db.get(ChatRun, journal.run_id))
            await db.commit()
            assert (await db.get(AgentTask, state.task.task_id)).status == expected
            assert (await db.get(AgentCapabilityCall, call_id)).status == ("unknown" if effect == "write" else "failed")
            assert result["unknown_write"] is (effect == "write") and not result["tools_replayed"]


async def test_lost_fence_prevents_dispatch_and_stale_receipt(tmp_path, monkeypatch):
    async with api_context(tmp_path / "lost-fence.db") as (_, factory):
        journal = await owned_journal(factory)
        async with factory() as db:
            session = await db.get(Session, "session-101")
            session.version += 1
            await db.commit()
        token = JOURNAL.set(journal)
        dispatch = AsyncMock()
        monkeypatch.setattr(mcp_client.TOOL_REGISTRY["query_order"], "execute_with_retry", dispatch)
        try:
            from app.agent.memory import SessionLeaseError
            with pytest.raises(SessionLeaseError):
                await mcp_client.invoke_tool("query_order", order_id="ORD960001")
            dispatch.assert_not_awaited()
        finally:
            JOURNAL.reset(token)


async def test_context_mutation_between_decision_and_execution_blocks_dispatch(monkeypatch):
    state, _ = request_state()
    remember_task_turn(state, "suggested_step", "尚未执行的其他建议")
    # A change to earlier sourced context must invalidate an in-flight plan.
    note = state.task.notes[0].model_copy(update={"turn_digest": "different", "content": "换成另一个任务"})
    state.task = state.task.model_copy(update={"notes": [note]})
    read = reader(monkeypatch)
    node, _ = executor()
    await node(state)
    read.assert_not_awaited()
    assert state.task.status == "stopped"


async def test_preview_commit_reply_loss_recovers_same_action_and_requires_confirmation(tmp_path):
    from app.services.refund_actions import prepare
    from fastapi import HTTPException
    async with api_context(tmp_path / "preview-commit.db") as (_, factory):
        journal = await owned_journal(factory)
        async with factory() as db:
            db.add(Order(order_id="ORD960001", user_id=101, status="已签收", status_code="delivered",
                total_amount="0.10", items=[], received_at=datetime.utcnow() - timedelta(days=1)))
            await db.commit()
        state, _ = request_state()
        state.run_id = journal.run_id
        token = TASK_STATE.set(state)
        try:
            call_id = await journal.begin("prepare_refund", {"order_id": "ORD960001"}, "write")
        finally:
            TASK_STATE.reset(token)
        payload = {"order_id": "ORD960001", "user_id": 101, "session_id": "session-101",
                   "reason": "外壳明显破损", "type": "return", "conditions": ["order_delivered"],
                   "request_key": "task-preview:" + journal.run_id}
        first = await prepare(factory, 101, payload)
        second = await prepare(factory, 101, payload)
        assert first["action_id"] == second["action_id"] and first["version"] == second["version"]
        with pytest.raises(HTTPException):
            await prepare(factory, 101, {**payload, "reason": "变更原请求原因"})
        # No journal.finish: simulate commit succeeded then process disappeared.
        async with factory() as db:
            result = await recover_calls(db, await db.get(ChatRun, journal.run_id))
            await db.commit()
            task = await db.get(AgentTask, state.task.task_id)
            assert task.status == "waiting_confirmation" and task.progress["action_id"] == first["action_id"]
            assert not result["unknown_write"] and not result["tools_replayed"]
            assert (await db.get(AgentCapabilityCall, call_id)).status == "succeeded"
            assert await db.scalar(select(func.count()).select_from(RefundAction)) == 1
            assert (await db.get(RefundAction, first["action_id"])).status == "pending"


async def test_caught_timeout_records_do_not_remain_running(tmp_path):
    from app.services.capability_journal import settle_open_calls
    async with api_context(tmp_path / "settle.db") as (_, factory):
        journal = await owned_journal(factory)
        read_id = await journal.begin("query_order", {"order_id": "ORD960001"}, "read")
        write_id = await journal.begin("prepare_refund", {"order_id": "ORD960001"}, "write")
        async with factory() as db:
            await settle_open_calls(db, await db.get(ChatRun, journal.run_id))
            await db.commit()
            assert (await db.get(AgentCapabilityCall, read_id)).status == "failed"
            assert (await db.get(AgentCapabilityCall, write_id)).status == "unknown"


@pytest.mark.parametrize("newer_version", [False, True])
async def test_recovery_resumes_current_task_but_cannot_overwrite_later_run(tmp_path, newer_version):
    async with api_context(tmp_path / "versions.db") as (_, factory):
        journal = await owned_journal(factory)
        state, _ = request_state()
        state.run_id = journal.run_id
        state.task = state.task.model_copy(update={"version": 2})
        async with factory() as db:
            db.add(AgentTask(id=state.task.task_id, user_id=101, actor_id=101, session_id=state.session_id,
                version=3 if newer_version else 2, status="waiting_input", progress=state.task.model_dump(),
                last_run_id=None))
            await db.commit()
        token = TASK_STATE.set(state)
        try:
            await journal.begin("query_order", {"order_id": "ORD960001"}, "read")
        finally:
            TASK_STATE.reset(token)
        async with factory() as db:
            result = await recover_calls(db, await db.get(ChatRun, journal.run_id))
            await db.commit()
            row = await db.get(AgentTask, state.task.task_id)
            if newer_version:
                assert not result["restored_tasks"] and row.last_run_id is None
            else:
                assert result["restored_tasks"] == [state.task.task_id] and row.last_run_id == journal.run_id


def test_0013_migration_is_repeatable_and_preserves_existing_run(tmp_path):
    from importlib.util import spec_from_file_location, module_from_spec
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text, inspect
    spec = spec_from_file_location("reliability_migration", Path(__file__).resolve().parents[1] / "alembic/versions/0013_agent_reliability.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite:///" + (tmp_path / "legacy.db").as_posix())
    try:
        with engine.begin() as db:
            for name in ("users", "sessions", "chat_runs"):
                db.execute(text(f"CREATE TABLE {name} (id VARCHAR(36) PRIMARY KEY)"))
            db.execute(text("INSERT INTO chat_runs VALUES ('legacy')"))
            with Operations.context(MigrationContext.configure(db)):
                module.upgrade()
                module.upgrade()
            assert "agent_capability_calls" in inspect(db).get_table_names()
            assert db.execute(text("SELECT id FROM chat_runs")).scalar_one() == "legacy"
    finally:
        engine.dispose()


@pytest.mark.parametrize("protocol",[False,True])
async def test_real_sql_tool_and_dispatch_journal_share_one_result(tmp_path,monkeypatch,protocol):
    async with api_context(tmp_path/"actual-tool.db") as (_,factory):
        journal=await owned_journal(factory)
        monkeypatch.setattr("app.utils.database.async_session",factory)
        monkeypatch.setattr(mcp_client,"_ENABLED",protocol)
        monkeypatch.setattr(mcp_client,"_TRANSPORT","inprocess")
        client=mcp_client._McpToolClient()
        monkeypatch.setattr(mcp_client,"_get_client",lambda:client)
        if protocol:
            assert await mcp_client.warmup_mcp_client()
        async with factory() as db:
            db.add(Order(order_id="ORD960001",user_id=101,status="已签收",status_code="delivered",total_amount="0.10",items=[]))
            await db.commit()
        token=JOURNAL.set(journal)
        try:
            payload={"user_id":101,"order_id":"ORD960001"}
            result=await mcp_client.invoke_tool("query_order",**payload,authorization=authorize_read("query_order",payload,101))
            assert result.success and result.data["status_code"]=="delivered"
            assert result.transport==("mcp" if protocol else "direct")
            async with factory() as db:
                row=await db.get(AgentCapabilityCall,result.call_id)
                assert row.status=="succeeded" and row.run_id==journal.run_id
                assert await db.scalar(select(func.count()).select_from(AgentCapabilityCall))==1
        finally:
            JOURNAL.reset(token)
            await client.aclose()
