"""实际Run/SQL/图与退款服务；模型为合成替身，Redis关闭。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from sqlalchemy import select, func
from app.agent.graph import AgentGraph
from app.agent.memory import SessionManager
from app.agent.state import AgentState
from app.models.models import ChatRun, User, Order, RefundAction, RefundApplication, HandoffRequestRecord, Ticket, OperationAudit
from app.schemas.schemas import ChatRequest
from app.services.chat_service import ChatService
from app.services import run_execution as runs
from app.services.refund_actions import prepare
from tests.test_ticket_idempotency import api_context


def graph_without_models(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    graph = AgentGraph()
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    monkeypatch.setattr(graph.llm_service, "run_structured", AsyncMock(return_value=None))
    monkeypatch.setattr(graph.llm_service, "is_available", lambda: False)
    return graph


async def test_shadow_notice_cannot_enter_handoff_when_review_rejects(monkeypatch):
    graph = graph_without_models(monkeypatch)
    reject = Mock(return_value=SimpleNamespace(
        passed=False, to_dict=lambda: {"passed": False, "issues": [], "overall_score": 0}
    ))
    monkeypatch.setattr(graph.validator, "validate", reject)
    handoff = AsyncMock(side_effect=AssertionError("shadow attempted business handoff"))
    graph.nodes["human_handoff"] = handoff
    graph.langgraph = graph._build_langgraph()
    try:
        state = await graph.run(AgentState(session_id="shadow", user_id=101,
                                           user_message="合成问题", understanding_mode="shadow"))
        assert state.reply == "影子理解已记录，本轮不执行申请、确认或取消。"
        assert not state.tool_calls and not state.need_human
        handoff.assert_not_awaited()
        reject.assert_not_called()
    finally:
        await graph.llm_service.close()


async def test_accepted_shadow_run_freezes_policy_then_new_rules_run_can_cancel(tmp_path, monkeypatch):
    from app.config.config import settings

    async with api_context(tmp_path / "policy.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        manager = SessionManager()
        manager._redis_available = False
        monkeypatch.setattr("app.services.chat_service.session_manager", manager)
        graph = graph_without_models(monkeypatch)
        service = ChatService(graph)
        observed = []
        actual = service.process

        async def record(state):
            observed.append((state.understanding_mode, state.strategy_version))
            return await actual(state)

        monkeypatch.setattr(service, "process", record)
        async with factory() as db:
            db.add(Order(order_id="ORD950002", user_id=101, status="已发货", status_code="shipped",
                         total_amount="0.10", items=[]))
            await db.commit()
        action = await prepare(factory, 101, dict(user_id=101, session_id="session-101",
                                                order_id="ORD950002", type="refund", reason="合成破损"))
        body = ChatRequest(user_id=101, session_id="session-101", message="确认",
                           action_id=action["action_id"], action_version=action["version"],
                           client_message_id="accepted-shadow")
        monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_MODE", "shadow")
        monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_VERSION", "shadow-frozen-v1")
        async with factory() as db:
            first, created = await runs.accept(db, await db.get(User, 101), body)
            assert created
        # 接受后改配置，原Run必须继续使用原模式，不能把“确认”变为真实提交。
        monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_MODE", "rules")
        monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_VERSION", "rollback-v2")
        try:
            await runs.execute(factory, first.id, service, manager, None, False)
            async with factory() as db:
                assert (await db.get(RefundAction, action["action_id"])).status == "pending"
                frozen = await db.get(ChatRun, first.id)
                assert frozen.status == "awaiting_confirmation"
                assert frozen.request_data["_strategy_version"] == "shadow-frozen-v1"
                replay, created = await runs.accept(db, await db.get(User, 101), body)
                assert not created and replay.id == first.id
                assert replay.request_data["_understanding_mode"] == "shadow"
                rollback, _ = await runs.accept(db, await db.get(User, 101), ChatRequest(
                    user_id=101, session_id="session-101", message="取消",
                    action_id=action["action_id"], action_version=action["version"],
                    client_message_id="rules-cancel"))
            await runs.execute(factory, rollback.id, service, manager, None, False)
            # 已完成Run再次交给执行器不得重做取消，亦不能采用后来切换的策略。
            monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_MODE", "model")
            await runs.execute(factory, rollback.id, service, manager, None, False)
            async with factory() as db:
                assert (await db.get(RefundAction, action["action_id"])).status == "cancelled"
                assert (await db.get(ChatRun, rollback.id)).request_data["_strategy_version"] == "rollback-v2"
                for model in [RefundApplication, Ticket, HandoffRequestRecord]:
                    assert await db.scalar(select(func.count()).select_from(model)) == 0
                assert await db.scalar(select(func.count()).select_from(OperationAudit).where(
                    OperationAudit.operation == "cancel_refund")) == 1
            assert observed == [("shadow", "shadow-frozen-v1"), ("rules", "rollback-v2")]
        finally:
            await graph.llm_service.close()


@pytest.mark.parametrize("mode,percentage,expected", [
    ("model", 0, "rules"), ("model", 100, "model"),
    ("shadow", 0, "shadow"), ("rules", 100, "rules"),
])
async def test_gray_rollout_endpoints_are_frozen_in_accepted_run(tmp_path, monkeypatch, mode, percentage, expected):
    from app.config.config import settings

    monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_MODE", mode)
    monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_PERCENTAGE", percentage)
    monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_VERSION", "rollout-fixed")
    async with api_context(tmp_path / "gray.db") as (_, factory):
        async with factory() as db:
            run, created = await runs.accept(db, await db.get(User, 101), ChatRequest(
                user_id=101, session_id="session-101", message="合成问题", client_message_id="fixed-gray"))
            assert created and run.request_data["_understanding_mode"] == expected
            assert run.request_data["_strategy_version"] == "rollout-fixed"


async def test_partial_gray_rollout_is_stable_for_same_user_across_sessions(tmp_path, monkeypatch):
    from app.config.config import settings

    monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_MODE", "model")
    monkeypatch.setattr(settings, "REQUEST_UNDERSTANDING_PERCENTAGE", 50)
    modes = []
    async with api_context(tmp_path / "partial-gray.db") as (_, factory):
        for index in range(12):
            async with factory() as db:
                # 不传session_id，实际创建12个不同会话，仍按同一用户稳定分桶。
                run, _ = await runs.accept(db, await db.get(User, 101), ChatRequest(
                    user_id=101, message="合成问题", client_message_id=f"gray-session-{index}"))
                modes.append(run.request_data["_understanding_mode"])
    assert modes[0] in {"model", "rules"} and len(set(modes)) == 1
