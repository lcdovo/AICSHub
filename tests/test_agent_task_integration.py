"""真实图、SQL、REST/WS与任务恢复；模型及知识依赖使用显式替身。"""
import asyncio
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, func
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import AgentGraph
from app.agent.request_understanding import TaskRequestUnderstanding
from app.agent.state import AgentState, AgentNode
from app.agent.task_contracts import NextStep, TaskDecision
from app.agent.task_loop import enter_task, progress
from app.agent.tools import ToolExecutionResult, QueryOrderTool, ApplyRefundTool
from app.config.config import settings
from app.models.models import AgentTask, AgentTaskStep, Order, RefundAction, RefundApplication, Ticket, HandoffRequestRecord
from app.services.agent_tasks import persist_task, load_pending_task
from tests.test_agent_task_loop import request_state, executor, reader, read_and_search
from tests.test_ticket_idempotency import api_context
from tests.test_m4_evidence_contract import result as knowledge_result
from tests.test_chat_turns import Validator
from tests.test_session_creation import session_api as session_api


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_TASK_LOOP_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_TASK_REFUND_PREVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_MODE", "model")
    monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_PERCENTAGE", 100)
    monkeypatch.setattr(settings, "USER_MEMORY_ENABLED", False)
    monkeypatch.setattr(settings, "REDIS_ENABLED", False)
    monkeypatch.setattr(settings, "LLM_JUDGE_CONTRACT_VERSION", "v1")


def configured_graph(monkeypatch, understanding, decisions):
    kb = SimpleNamespace(async_search=AsyncMock(return_value=knowledge_result()), evidence_is_current=lambda _: True)
    monkeypatch.setattr("app.agent.graph.get_knowledge_base", lambda: kb)
    graph = AgentGraph()
    graph.checkpointer = InMemorySaver()
    model = graph.llm_service
    pending = iter(decisions)

    async def structured(prompt, payload, schema, **kwargs):
        if schema is TaskRequestUnderstanding:
            return understanding
        assert issubclass(schema, TaskDecision)
        return schema.model_validate({"step": next(pending).model_dump(exclude_defaults=True)})

    monkeypatch.setattr(model, "run_structured", AsyncMock(side_effect=structured))
    graph.validator = Validator()
    graph.llm_judge = SimpleNamespace(judge=AsyncMock(return_value={"grounded": True, "fact_score": 1.,
        "refuse_recommended": False, "has_unsupported_claim": False}), DEFAULT_THRESHOLD=.45)
    response = graph.nodes[AgentNode.RESPONSE_GENERATION]
    response.generate = AsyncMock(return_value="请先按说明检查设备电源连接，然后告诉我指示灯是否亮起。")
    graph.langgraph = graph._build_langgraph()
    return graph, kb, response


def diagnostic():
    return TaskRequestUnderstanding(topic="technical", request_type="inquiry", entities={"order_id": None, "product": "耳机"},
        conditions=[], subrequests=[], service_preference="unspecified", emotion="neutral", clarification=None,
        retrieval_query="耳机无法开机如何排查", entity_source="current_message", task_kind="troubleshoot")


@pytest.mark.parametrize("order_first", [False, True])
async def test_actual_graph_answers_order_and_knowledge_in_either_request_order(monkeypatch, order_first):
    requests = [{"topic": "query_order", "request_type": "query"},
                {"topic": "refund", "request_type": "inquiry"}]
    if not order_first:
        requests.reverse()
    message = ("查询订单ORD960001，再告诉我退货是否要保留包装。" if order_first
               else "退货是否要保留包装？再查询订单ORD960001。")
    understanding = TaskRequestUnderstanding(**requests[0], entities={"order_id": "ORD960001", "product": None},
        conditions=[], subrequests=requests, service_preference="unspecified", emotion="neutral",
        clarification=None, retrieval_query="退货保留包装的要求", entity_source="current_message", task_kind="multi")
    next_step = (NextStep(action="search_knowledge", knowledge_focus="request") if order_first else
                 NextStep(action="read", reads=[{"tool": "query_order", "order_id": "ORD960001"}]))
    graph, kb, response = configured_graph(monkeypatch, understanding, [next_step, NextStep(action="finish")])
    order_read = reader(monkeypatch)
    response.generate.return_value = "订单ORD960001目前已签收；退货包装要求请参考本轮知识说明。"
    try:
        result = await graph.run(AgentState(session_id="multi-" + str(order_first), user_id=101,
            actor_id=101, source_message_id=17, user_message=message, understanding_mode="model"))
        assert result.task.status == "completed" and result.review_result.passed
        assert order_read.await_count == kb.async_search.await_count == 1
        assert result.task_runtime.tool_calls == 2 and result.task_runtime.decisions == 2
        assert graph.llm_service.run_structured.await_count == 3
        assert {call.tool_name for call in result.tool_calls} == {"query_order", "hybrid_search"}
        review = graph.llm_judge.judge.call_args.kwargs
        assert message in review["user_query"] and "subrequests" in review["user_query"]
        assert "ORD960001" in review["tool_evidence"] and review["kb_context"]
        assert "已签收" in result.reply and "包装" in result.reply
        assert not result.pending_confirmations and not result.need_human
    finally:
        await graph.llm_service.close()


async def test_actual_graph_diagnostic_waits_for_feedback_and_revalidates_response(monkeypatch):
    graph, kb, response = configured_graph(monkeypatch, diagnostic(), [NextStep(action="ask_user", question="diagnostic_feedback")])
    try:
        state = await graph.run(AgentState(session_id="diagnostic", user_id=101, user_message="耳机无法开机如何排查", understanding_mode="model"))
        assert state.current_node == AgentNode.END and state.task.status == "waiting_input"
        assert state.task.waiting_for == "diagnostic_feedback"
        assert state.task_runtime.decisions == 1 and state.task_runtime.tool_calls == 1
        assert kb.async_search.await_count == response.generate.await_count == 1
        assert state.review_result.passed and graph.llm_judge.judge.await_count == 1
        assert "一个" in response._build_llm_prompt(state) and "等待下一轮反馈" in response._build_llm_prompt(state)
        assert not state.need_human and not state.pending_confirmations
        fresh = AgentState.model_validate_json(state.model_dump_json()).for_next_turn(101, "还是不行")
        assert fresh.task and fresh.task_runtime is None and not fresh.current_evidence()
    finally:
        await graph.llm_service.close()


@pytest.mark.parametrize("step", [NextStep(action="ask_user", question="diagnostic_feedback"),
                                  NextStep(action="search_knowledge", knowledge_focus="troubleshooting")])
async def test_rejected_final_answer_does_not_complete_task_or_create_handoff(monkeypatch, step):
    graph, _, response = configured_graph(monkeypatch, diagnostic(), [step])
    graph.llm_judge.judge.return_value = None
    try:
        state = await graph.run(AgentState(session_id="reject", user_id=101, user_message="耳机无法开机如何排查", understanding_mode="model"))
        assert state.task.status == "stopped" and state.task.stop_reason == "final_response_not_accepted"
        assert not state.need_human and not state.pending_confirmations
        assert state.review_result.status == "unknown"
        assert response.generate.await_count == 1
        assert not any(c.tool_name in {"create_ticket", "escalate_to_human", "apply_refund"} for c in state.tool_calls)
    finally:
        await graph.llm_service.close()


async def test_sql_progress_and_steps_commit_together_and_cas_blocks_stale_worker(tmp_path, monkeypatch):
    from app.agent.memory import SessionLeaseError
    async with api_context(tmp_path / "task-cas.db") as (_, factory):
        reader(monkeypatch)
        state, _ = request_state(parameters={"refund_type": "return"}, missing_information=["reason"])
        node, _ = executor()
        await read_and_search(state, node)
        state.task_runtime.next_step = NextStep(action="ask_user", question="reason")
        await node(state)
        async with factory() as db:
            await persist_task(db, state)
            await db.rollback()
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(AgentTask)) == 0
            assert await db.scalar(select(func.count()).select_from(AgentTaskStep)) == 0
        # 回滚后的调用方不复用已变更的版本；创建新独立执行状态。
        state.task = state.task.model_copy(update={"version": 0})
        async with factory() as db:
            await persist_task(db, state)
            await db.commit()
        async with factory() as db:
            restored = await load_pending_task(db, 101, "session-101")
            assert restored.task_id == state.task.task_id and restored.conditions == ["order_delivered"]
            assert await load_pending_task(db, 202, "session-101") is None
            assert await load_pending_task(db, 101, "session-202") is None
            assert await db.scalar(select(func.count()).select_from(AgentTaskStep)) == 3
            serialized = json.dumps((await db.get(AgentTask, restored.task_id)).progress)
            assert "evidence" not in serialized and "confirmation" not in serialized and "total_amount" not in serialized
        stale = state.model_copy(deep=True)
        # 模拟下一Run更新进度；不能把旧Run的同一组步骤再插入一次。
        state.source_message_id = 18
        state.task_runtime.steps = []
        async with factory() as db:
            await persist_task(db, state)
            await db.commit()
        async with factory() as db:
            with pytest.raises(SessionLeaseError, match="版本"):
                await persist_task(db, stale)
            await db.rollback()


@pytest.mark.parametrize("foreign", [False, True])
async def test_actual_sql_owner_and_refund_domain_checks_still_apply(tmp_path, monkeypatch, foreign):
    from app.agent.nodes.tool_node import persistent_actions
    async with api_context(tmp_path / "domain.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        async with factory() as db:
            db.add(Order(order_id="ORD960001", user_id=202 if foreign else 101, status="已签收", status_code="delivered",
                         total_amount="0.10", items=[], received_at=datetime.utcnow() - timedelta(days=1)))
            await db.commit()

        async def invoke(name, **payload):
            assert name == "query_order"
            result = await QueryOrderTool().execute(**payload)
            return ToolExecutionResult(**{k: v for k, v in result.items() if k in ToolExecutionResult.model_fields})

        monkeypatch.setattr("app.agent.mcp_client.invoke_tool", invoke)
        state, _ = request_state()
        node, _ = executor(actions=persistent_actions())
        if foreign:
            await node(state)
            from app.agent.task_loop import observe_task
            await observe_task(state)
            assert state.task.status == "stopped" and not state.pending_confirmations
        else:
            await read_and_search(state, node)
            state.task_runtime.next_step = NextStep(action="prepare_refund")
            assert await node(state) == AgentNode.CONFIRMATION
            assert state.task.status == "waiting_confirmation"
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(RefundAction)) == (0 if foreign else 1)
            for entity in (RefundApplication, Ticket, HandoffRequestRecord):
                assert await db.scalar(select(func.count()).select_from(entity)) == 0


@pytest.mark.parametrize("continue_task", [False, True])
def test_reason_followup_cannot_drop_previous_condition(continue_task):
    state, _ = request_state()
    progress(state, status="waiting_input", waiting_for="reason")
    fresh = state.for_next_turn(101, "外壳破损")
    fresh.actor_id = 101
    fresh.understanding_mode = "model"
    value = state.understanding.model_copy(update={"continue_task": continue_task, "conditions": [], "entity_source": "history"})
    from app.agent.request_understanding import route_understanding
    assert enter_task(fresh, route_understanding(fresh, value)) == AgentNode.RESPONSE_GENERATION
    assert fresh.needs_clarification and not fresh.refund_preview_data()


@pytest.mark.parametrize("transport", ["rest", "ws"])
def test_actual_chat_run_persists_task_and_replay_does_not_replan(session_api, monkeypatch, transport):
    from app.security import create_access_token
    from tests.test_chat_auth import receive_turn
    import app.routers.chat as chat

    client, factory, manager, redis, users = session_api
    graph, _, _ = configured_graph(monkeypatch, diagnostic(), [NextStep(action="ask_user", question="diagnostic_feedback")])
    monkeypatch.setattr(chat.chat_service, "process", graph.run)
    monkeypatch.setattr(chat.chat_service, "process_stream", graph.run_stream)
    sid = client.post("/api/v1/chat/sessions?user_id=101").json()["data"]["id"]
    body = {"user_id": 101, "session_id": sid, "message": "耳机无法开机如何排查", "client_message_id": "task-once"}
    try:
        if transport == "rest":
            response = client.post("/api/v1/chat/send", json=body)
            assert response.status_code == 200, response.text
            data = response.json()
        else:
            with client.websocket_connect("/api/v1/chat/stream?access_token=" + create_access_token(users[0])) as ws:
                ws.send_json(body)
                events = receive_turn(ws)
            data = next(e for e in events if e["type"] == "done")
        assert data["task_status"] == "waiting_input"
        task_id = data["task_id"]
        assert graph.llm_service.run_structured.await_count == 2
        replay = client.post("/api/v1/chat/send", json=body)
        assert replay.status_code == 200 and replay.json()["task_id"] == task_id
        assert graph.llm_service.run_structured.await_count == 2
        task = client.get("/api/v1/chat/tasks/" + task_id)
        assert task.status_code == 200 and len(task.json()["data"]["steps"]) == 2
        assert client.get("/api/v1/chat/tasks/" + task_id, headers={"X-Test-Actor": "202"}).status_code == 403

        async def restore():
            manager._state_cache.clear()
            redis.values.clear()
            state = await chat.chat_service.hydrate_state(sid, 101, "还是不行", recover_from_db=True, task_loop_enabled=True)
            assert state.task.task_id == task_id and state.task_runtime is None
            assert not state.current_evidence() and not state.tool_calls
            async with factory() as db:
                row = await db.get(AgentTask, task_id)
                assert row.last_run_id and row.version == 1
                assert await db.scalar(select(func.count()).select_from(AgentTask)) == 1
        asyncio.run(restore())
    finally:
        asyncio.run(graph.llm_service.close())


@pytest.mark.parametrize("transport", ["rest", "ws"])
@pytest.mark.parametrize("change_status", [False, True], ids=["condition-holds", "condition-changed"])
def test_full_after_sales_requires_fresh_confirmation_before_sql_submission(session_api, monkeypatch, transport, change_status):
    from app.security import create_access_token
    from tests.test_chat_auth import receive_turn
    import app.routers.chat as chat
    client, factory, _, _, users = session_api
    seed_state, _ = request_state()
    graph, _, _ = configured_graph(monkeypatch, seed_state.understanding, [
        NextStep(action="search_knowledge", knowledge_focus="refund_policy"), NextStep(action="prepare_refund")])
    monkeypatch.setattr(chat.chat_service, "process", graph.run)
    monkeypatch.setattr(chat.chat_service, "process_stream", graph.run_stream)

    async def seed():
        async with factory() as db:
            db.add(Order(order_id="ORD960001", user_id=101, status="已签收", status_code="delivered",
                         total_amount="0.10", items=[], received_at=datetime.utcnow() - timedelta(days=1)))
            await db.commit()

    asyncio.run(seed())

    async def invoke(name, **payload):
        assert name in {"query_order", "apply_refund"}
        result = await (QueryOrderTool() if name == "query_order" else ApplyRefundTool()).execute(**payload)
        return ToolExecutionResult(**{k: v for k, v in result.items() if k in ToolExecutionResult.model_fields})

    monkeypatch.setattr("app.agent.mcp_client.invoke_tool", invoke)
    # 旧确定确认链的ToolExecutionDependencies在注册时捕获了兼容invoke函数，内部动态查MCP。
    monkeypatch.setattr("app.agent.nodes.tool_node.invoke_tool", invoke)
    sid = client.post("/api/v1/chat/sessions?user_id=101").json()["data"]["id"]
    body = {"user_id": 101, "session_id": sid, "message": seed_state.user_message, "client_message_id": "after-sales"}

    def send(payload):
        if transport == "rest":
            response = client.post("/api/v1/chat/send", json=payload)
            assert response.status_code == 200, response.text
            return response.json()
        with client.websocket_connect("/api/v1/chat/stream?access_token=" + create_access_token(users[0])) as ws:
            ws.send_json(payload)
            return next(e for e in receive_turn(ws) if e["type"] == "done")

    async def inspect(expected_status, applications):
        async with factory() as db:
            task = await db.scalar(select(AgentTask))
            assert task.status == expected_status
            assert await db.scalar(select(func.count()).select_from(RefundAction)) == 1
            assert await db.scalar(select(func.count()).select_from(RefundApplication)) == applications
            assert await db.scalar(select(func.count()).select_from(Ticket)) == 0
            assert await db.scalar(select(func.count()).select_from(HandoffRequestRecord)) == 0
            return task.id

    try:
        first = send(body)
        assert first["task_status"] == "waiting_confirmation" and "确认" in first["reply"]
        task_id = asyncio.run(inspect("waiting_confirmation", 0))
        calls = graph.llm_service.run_structured.await_count
        assert calls == 3  # 一次理解、两次下一步决策，无额外规划首步。
        if change_status:
            async def change():
                async with factory() as db:
                    order = await db.scalar(select(Order).where(Order.order_id == "ORD960001"))
                    order.status_code = "shipped"  # 仍满足普通退款资格，但不满足原“已签收”条件。
                    await db.commit()
            asyncio.run(change())
            rejected = send({**body, "message": "确认", "client_message_id": "condition-changed"})
            assert rejected["task_status"] == "waiting_confirmation"
            assert "校验" in rejected["reply"] and "未登记新申请" in rejected["reply"]
            asyncio.run(inspect("waiting_confirmation", 0))
            assert graph.llm_service.run_structured.await_count == calls
            return
        second = send({**body, "message": "确认", "client_message_id": "confirm-after-sales"})
        assert second["task_id"] == task_id and second["task_status"] == "completed", second
        asyncio.run(inspect("completed", 1))
        assert graph.llm_service.run_structured.await_count == calls
        send({**body, "message": "确认", "client_message_id": "confirm-after-sales"})
        asyncio.run(inspect("completed", 1))
        assert graph.llm_service.run_structured.await_count == calls
    finally:
        asyncio.run(graph.llm_service.close())
