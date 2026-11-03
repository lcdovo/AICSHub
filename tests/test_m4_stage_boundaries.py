"""实际图/Run/SQL与业务提交，生成/审查故障为替身。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select, func
from app.agent.graph import AgentGraph
from app.agent.memory import SessionManager
from app.agent.state import AgentState, AgentNode
from app.agent.request_understanding import RequestUnderstanding
from app.agent.tools import CreateTicketTool, ApplyRefundTool, ToolExecutionResult
from app.models.models import User, ChatRun, Ticket, OperationAudit, HandoffRequestRecord, Order, RefundApplication, RefundAction, Message
from app.schemas.schemas import ChatRequest
from app.services.chat_service import ChatService
from app.services import run_execution as runs
from tests.test_ticket_idempotency import api_context


def graph_with_memory():
    from langgraph.checkpoint.memory import InMemorySaver
    graph = AgentGraph()
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    return graph


@pytest.mark.parametrize("second_passes", [False, True])
async def test_response_regeneration_never_reenters_committed_tool_stage(tmp_path, monkeypatch, second_passes):
    from app.config.config import settings
    import app.agent.nodes.tool_node as tool_node
    import app.agent.nodes.handoff_node as handoff_node

    async with api_context(tmp_path / "stage.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_MODE", "model")
        manager = SessionManager()
        manager._redis_available = False
        monkeypatch.setattr("app.services.chat_service.session_manager", manager)
        graph = graph_with_memory()
        understanding = RequestUnderstanding(
            topic="complaint", request_type="apply", entities={"order_id": None, "product": None},
            conditions=[], subrequests=[], service_preference="unspecified", emotion="negative",
            clarification=None, retrieval_query="创建投诉", entity_source="none")
        monkeypatch.setattr(graph.llm_service, "run_structured", AsyncMock(return_value=understanding))
        monkeypatch.setattr(graph.llm_service, "is_available", lambda: True)
        executions = []

        async def invoke(name, **payload):
            assert name == "create_ticket"
            executions.append(name)
            actual = await CreateTicketTool().execute(**payload)
            return ToolExecutionResult(**{k: v for k, v in actual.items() if k in ToolExecutionResult.model_fields})

        monkeypatch.setattr(tool_node, "invoke_tool", invoke)
        handoff = AsyncMock(return_value=ToolExecutionResult(success=False, error="synthetic unavailable"))
        monkeypatch.setattr(handoff_node, "invoke_tool", handoff)
        # 强制进入实际生成分支（正常有回执模板可快捷），模拟第一次生成被审查拒绝。
        monkeypatch.setattr(graph.nodes[AgentNode.RESPONSE_GENERATION], "tool_reply", lambda _: None)
        drafts = []

        async def generate(state):
            drafts.append(state.collected_info["create_ticket_result"]["ticket_id"])
            return "合成生成结果" + str(len(drafts))

        monkeypatch.setattr(graph.nodes[AgentNode.RESPONSE_GENERATION], "generate", generate)
        validations = []

        def review(**kwargs):
            validations.append(kwargs["response"])
            passed = second_passes and len(validations) == 2
            return SimpleNamespace(passed=passed, to_dict=lambda: {
                "passed": passed, "issues": [], "overall_score": 100 if passed else 0})

        monkeypatch.setattr(graph.validator, "validate", review)
        try:
            async with factory() as db:
                run, _ = await runs.accept(db, await db.get(User, 101), ChatRequest(
                    user_id=101, session_id="session-101", message="我要正式投诉服务问题",
                    client_message_id="regenerate-once"))
            await runs.execute(factory, run.id, ChatService(graph), manager, None, False)
            async with factory() as db:
                current = await db.get(ChatRun, run.id)
                assert current.status == "succeeded"
                if second_passes:
                    assert current.result["reply"] == "合成生成结果2"
                else:
                    assert "无法确认" in current.result["reply"]
                assert await db.scalar(select(func.count()).select_from(Ticket)) == 1
                assert await db.scalar(select(func.count()).select_from(HandoffRequestRecord)) == 0
                audit = (await db.scalars(select(OperationAudit))).all()
                assert len([r for r in audit if r.operation == "create_ticket"]) == 1
            assert executions == ["create_ticket"]
            assert len(drafts) == 2 and drafts[0] == drafts[1]
            assert validations == ["合成生成结果1", "合成生成结果2"]
            assert handoff.await_count == (0 if second_passes else 1)
        finally:
            await graph.llm_service.close()


async def test_graph_maximum_steps_ends_loop_without_calling_twenty_fifth_node():
    graph = graph_with_memory()
    calls = []

    async def repeat(state):
        calls.append(state.node_steps)
        return AgentNode.TOOL_EXECUTION

    graph.nodes[AgentNode.START] = repeat
    graph.nodes[AgentNode.TOOL_EXECUTION] = repeat
    graph.langgraph = graph._build_langgraph()
    try:
        state = await graph.run(AgentState(session_id="loop", user_id=101, user_message="合成循环"))
        assert len(calls) == 24 and state.node_steps == 25
        assert state.current_node == AgentNode.END and state.response_ready
        assert "步骤已达上限" in state.reply
    finally:
        await graph.llm_service.close()


async def test_refund_confirmation_commits_once_even_when_answer_regenerates(tmp_path, monkeypatch):
    from app.services.refund_actions import prepare
    import app.agent.nodes.tool_node as tool_node

    async with api_context(tmp_path / "refund-stage.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        manager = SessionManager()
        manager._redis_available = False
        monkeypatch.setattr("app.services.chat_service.session_manager", manager)
        async with factory() as db:
            db.add(Order(order_id="ORD950004", user_id=101, total_amount="0.10",
                         status="已发货", status_code="shipped", items=[]))
            await db.commit()
        action = await prepare(factory, 101, dict(user_id=101, session_id="session-101",
                                                order_id="ORD950004", type="refund", reason="合成商品严重破损"))
        graph = graph_with_memory()
        monkeypatch.setattr(graph.nodes[AgentNode.RESPONSE_GENERATION], "tool_reply", lambda _: None)
        calls = []

        async def invoke(name, **payload):
            assert name == "apply_refund"
            calls.append(name)
            result = await ApplyRefundTool().execute(**payload)
            return ToolExecutionResult(**{k: v for k, v in result.items() if k in ToolExecutionResult.model_fields})

        monkeypatch.setattr(tool_node, "invoke_tool", invoke)
        drafts = []

        async def generate(state):
            drafts.append(state.collected_info["apply_refund_result"]["refund_id"])
            return "合成模拟退款申请回执" + str(len(drafts))

        monkeypatch.setattr(graph.nodes[AgentNode.RESPONSE_GENERATION], "generate", generate)
        reviews = []

        def review(**kwargs):
            reviews.append(kwargs["response"])
            passed = len(reviews) == 2
            return SimpleNamespace(passed=passed, to_dict=lambda: {"passed": passed, "issues": [], "overall_score": 80})

        monkeypatch.setattr(graph.validator, "validate", review)
        try:
            async with factory() as db:
                run, _ = await runs.accept(db, await db.get(User, 101), ChatRequest(
                    user_id=101, session_id="session-101", message="确认", action_id=action["action_id"],
                    action_version=action["version"], client_message_id="refund-regenerate"))
            await runs.execute(factory, run.id, ChatService(graph), manager, None, False)
            async with factory() as db:
                current = await db.get(ChatRun, run.id)
                assert current.status == "succeeded" and current.result["reply"] == "合成模拟退款申请回执2"
                assert await db.scalar(select(func.count()).select_from(RefundApplication)) == 1
                audits = (await db.scalars(select(OperationAudit).where(
                    OperationAudit.operation == "apply_refund"))).all()
                assert len(audits) == 1
                assert audits[0].request_payload["message_id"] == current.user_message_id
                assert (await db.get(Message, current.user_message_id)).content == "确认"
                persisted = await db.get(RefundAction, action["action_id"])
                assert persisted.status == "submitted" and persisted.application_id == drafts[0]
            assert calls == ["apply_refund"] and len(drafts) == 2 and drafts[0] == drafts[1]
        finally:
            await graph.llm_service.close()


async def test_handoff_failure_is_terminal_without_second_handoff(monkeypatch):
    import app.agent.nodes.handoff_node as handoff_node

    graph = graph_with_memory()
    value = RequestUnderstanding(
        topic="human", request_type="apply", entities={"order_id": None, "product": None},
        conditions=[], subrequests=[], service_preference="human", emotion="neutral",
        clarification=None, retrieval_query="请求人工", entity_source="none")
    monkeypatch.setattr(graph.llm_service, "run_structured", AsyncMock(return_value=value))
    monkeypatch.setattr(handoff_node, "authorize_handoff", lambda *args: "synthetic-bound-auth")
    tool = AsyncMock(return_value=ToolExecutionResult(success=False, error="synthetic unavailable"))
    monkeypatch.setattr(handoff_node, "invoke_tool", tool)
    try:
        state = await graph.run(AgentState(session_id="handoff-fail", user_id=101,
                                           user_message="请转真人", understanding_mode="model"))
        assert state.current_node == AgentNode.END and state.node_steps < 10
        tool.assert_awaited_once()
        assert len(state.tool_calls) == 1 and not state.tool_calls[0].success
        assert "无法确认" in state.reply
    finally:
        await graph.llm_service.close()
