import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from sqlalchemy import select, func
from app.agent.read_plan import ReadTask, execute_read_plan
from app.agent.request_understanding import RequestUnderstanding, validate_semantics
from app.agent.state import AgentState
from app.agent.graph import AgentGraph
from app.models.models import Order, Ticket, RefundAction, RefundApplication, HandoffRequestRecord
from tests.test_ticket_idempotency import api_context


def understanding(**changes):
    fields = dict(topic="query_order", request_type="query",
                  entities={"order_id": "ORD950003", "product": None}, conditions=[],
                  subrequests=[], service_preference="unspecified", emotion="neutral",
                  clarification=None, retrieval_query="查订单ORD950003和我的咨询历史",
                  entity_source="current_message", read_tasks=[
                      {"tool": "query_order", "resource_id": "ORD950003"},
                      {"tool": "get_user_history"},
                  ])
    fields.update(changes)
    return RequestUnderstanding.model_validate(fields)


async def test_failed_read_cancels_and_joins_other_task_before_returning():
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def reader(task, actor):
        if task.tool == "query_order":
            await entered.wait()
            raise RuntimeError("synthetic failure")
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    with pytest.raises(RuntimeError, match="synthetic failure"):
        await execute_read_plan([ReadTask(tool="query_order"), ReadTask(tool="get_user_history")], None, reader)
    assert cleaned.is_set()


async def test_parent_run_cancellation_joins_both_reads():
    count = 0
    entered = asyncio.Event()
    cleaned = []

    async def reader(task, actor):
        nonlocal count
        try:
            count += 1
            if count == 2:
                entered.set()
            await asyncio.Event().wait()
        finally:
            cleaned.append(task.tool)

    parent = asyncio.create_task(execute_read_plan(
        [ReadTask(tool="query_order"), ReadTask(tool="get_user_history")], None, reader))
    await asyncio.wait_for(entered.wait(), 2)
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parent
    assert set(cleaned) == {"query_order", "get_user_history"}


@pytest.mark.parametrize("fields", [
    {"request_type": "apply"},
    {"read_tasks": [{"tool": "query_order", "resource_id": "ORDOTHER"}]},
    {"read_tasks": [{"tool": "get_user_history", "resource_id": "202"}]},
    {"read_tasks": [{"tool": "get_user_history"}, {"tool": "get_user_history"}]},
])
def test_plan_cannot_change_query_into_write_or_add_unobserved_resource(fields):
    state = AgentState(session_id="s", user_id=101, user_message="查订单ORD950003和我的咨询历史")
    with pytest.raises(ValueError):
        validate_semantics(understanding(**fields), state)


@pytest.mark.parametrize("task", [
    {"tool": "apply_refund"}, {"tool": "exec"},
    {"tool": "get_user_history", "user_id": 202},
])
def test_model_schema_forbids_unregistered_tools_or_principal(task):
    with pytest.raises(ValueError):
        understanding(read_tasks=[task])


@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("conditional", [False, True])
async def test_main_graph_parallel_reads_use_live_owner_scope_and_never_write(tmp_path, monkeypatch, foreign, conditional):
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent.tools import QueryOrderTool, GetUserHistoryTool, ToolExecutionResult
    import app.agent.mcp_client as mcp

    async with api_context(tmp_path / "reads.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        async with factory() as db:
            db.add(Order(order_id="ORD950003", user_id=202 if foreign else 101,
                         total_amount="0.10", status="已发货", status_code="shipped", items=[]))
            await db.commit()
        history_seen = []

        class Memory:
            async def get_user_profile(self, **payload):
                history_seen.append(payload)
                return {"history": [{"role": "user", "content": "合成本人旧问题"}],
                        "memory_source": "sql"}

        monkeypatch.setattr("app.services.user_memory.get_user_memory_service", lambda: Memory())
        entered = 0
        together = asyncio.Event()
        tools = {"query_order": QueryOrderTool(), "get_user_history": GetUserHistoryTool()}

        async def invoke(name, **payload):
            nonlocal entered
            assert name in tools
            entered += 1
            if entered == 2:
                together.set()
            await asyncio.wait_for(together.wait(), 2)
            result = await tools[name].execute(**payload)
            return ToolExecutionResult(**{k: v for k, v in result.items() if k in ToolExecutionResult.model_fields})

        monkeypatch.setattr(mcp, "invoke_tool", invoke)
        graph = AgentGraph()
        from tests.test_chat_turns import allowed_judge
        graph.llm_judge = allowed_judge()
        graph.checkpointer = InMemorySaver()
        graph.langgraph = graph._build_langgraph()
        value = understanding(conditions=[{"check": "order_not_shipped", "source": "current_message"}],
                              subrequests=[{"topic": "refund", "request_type": "apply"}]) if conditional else understanding()
        monkeypatch.setattr(graph.llm_service, "run_structured", AsyncMock(return_value=value))
        # 整图仍执行真实输出校验；这条回复来自读回执，无供应商草稿。
        monkeypatch.setattr(graph.llm_service, "is_available", lambda: True)
        try:
            state = await graph.run(AgentState(session_id="session-101", user_id=101, actor_id=101,
                                               user_message="查订单ORD950003和我的咨询历史",
                                               understanding_mode="model"))
            assert entered == 2 and len(state.tool_calls) == 2
            assert all(t.arguments["user_id"] == 101 for t in state.tool_calls)
            assert history_seen[0]["user_id"] == 101
            if foreign:
                assert not state.tool_calls[0].success
                assert "0.10" not in state.reply and "已发货" not in state.reply
            else:
                assert state.tool_calls[0].success and "0.10" in state.reply
                assert "合成本人旧问题" in state.reply
            if conditional:
                assert "申请尚未准备或提交" in state.reply
            fresh = state.for_next_turn(101, "新问题")
            assert not fresh.read_plan and not fresh.tool_calls
            async with factory() as db:
                for model in [Ticket, RefundAction, RefundApplication, HandoffRequestRecord]:
                    assert await db.scalar(select(func.count()).select_from(model)) == 0
        finally:
            await graph.llm_service.close()


async def test_read_plan_review_failure_clarifies_without_handoff(monkeypatch):
    from app.agent.nodes.response_node import response_generation_node
    from app.agent.state import AgentNode

    graph = AgentGraph()
    monkeypatch.setattr(graph.validator, "validate", Mock(return_value=SimpleNamespace(
        passed=False, to_dict=lambda: {"passed": False, "issues": [], "overall_score": 0})))
    state = AgentState(session_id="s", user_id=101, user_message="查订单ORD950003",
                       reply="合成待审查查询结果", read_plan=[ReadTask(tool="query_order", resource_id="ORD950003")])
    try:
        assert await response_generation_node(graph, state) == AgentNode.END
        assert state.needs_clarification and not state.need_human
        assert state.collected_info["validation"]["passed"] is False
        assert "未通过校验" in state.reply
    finally:
        await graph.llm_service.close()
