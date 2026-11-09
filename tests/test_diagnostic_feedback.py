"""有限反馈来源规则及最终发布回归；不以规则覆盖声称一般语义正确。"""
from unittest.mock import AsyncMock

import pytest

from app.agent.diagnostic_feedback import diagnostic_feedback_contract, diagnostic_claim_issues
from app.agent.answer_contracts import AnswerDraft, answer_requirements
from app.agent.answer_review import validate_candidate
from app.agent.answer_publication import publish_answer
from app.agent.nodes.tool_node import verify_tool_result
from app.agent.task_contracts import TaskProgress, TaskRuntime
from app.agent.state import AgentState, AgentNode, ToolCall
from tests.test_answer_pipeline import component
from app.config.config import settings


def feedback(message="检查之后仍然断线。"):
    return AgentState(session_id="diagnostic-sources", user_id=101, actor_id=101, user_message=message,
        task=TaskProgress(task_id="task1", session_id="diagnostic-sources", user_id=101, actor_id=101,
                          goal="路由器频繁断线", kind="troubleshoot", status="waiting_input"),
        task_runtime=TaskRuntime(scope_digest="s", understanding_digest="u", parameters_digest="p", task_spec_digest="t"))


@pytest.mark.parametrize("response", [
    "电源这一步已排除，我们继续下一步。", "已经排除了电源问题。", "电源已确认正常。",
    "电源正常，请检查连接。", "网络连接已经正常。", "指示灯没有问题。", "供电无异常。",
])
def test_checking_does_not_prove_normal_or_eliminated(response):
    assert diagnostic_claim_issues(response, diagnostic_feedback_contract(feedback()))


@pytest.mark.parametrize("response", [
    "请先确认电源是否正常。", "电源正常吗？请告诉我指示灯情况。",
    "如果电源正常，请再检查连接。", "电源尚未确认正常。", "不能说电源已排除。",
    "检查后仍断线，暂时无法判断电源是否正常。", "请检查电源连接并观察指示灯。",
])
def test_questions_hypotheses_and_unknown_results_remain_valid(response):
    assert not diagnostic_claim_issues(response, diagnostic_feedback_contract(feedback()))


@pytest.mark.parametrize("message,expected", [
    ("电源正常，指示灯不亮。", True), ("电源已确认正常，仍然断线。", True),
    ("供电没有问题。", True), ("电源无异常。", True),
    ("电源正常吗？", False), ("如果电源正常怎么办？", False),
    ("客服说电源正常。", False), ("电源之前正常。", False),
    ("电源正常，但现在电源异常。", False), ("电源正常，但电源检查后仍断线。", False),
    ("检查电源后还是不亮。", False),
])
def test_only_current_explicit_user_result_supports_normal(message, expected):
    state = feedback(message)
    state.messages = [{"role": "assistant", "content": "电源已排除。"},
                      {"role": "user", "content": "昨天电源正常。"}]
    state.conversation_summary = "电源已经正常"
    contract = diagnostic_feedback_contract(state)
    power = next(row for row in contract["components"] if row["component"] == "power")
    assert power["current_user_reported_normal"] is expected
    assert bool(diagnostic_claim_issues("你反馈电源正常。", contract)) is not expected
    assert diagnostic_claim_issues("电源已经排除。", contract)


async def test_unsupported_claim_rejected_before_judge_even_if_judge_would_accept(monkeypatch):
    monkeypatch.setattr(settings, "LLM_JUDGE_CONTRACT_VERSION", "v1")
    state = feedback()
    state.reply = "电源这一步已排除。"
    node, model, _ = component({"grounded": True, "fact_score": 1.,
        "has_unsupported_claim": False, "refuse_recommended": False, "reason": "synthetic"})
    assert not await validate_candidate(node, state)
    assert "diagnostic_unproven_elimination" in state.review_result.issue_codes
    assert state.review_result.repairable
    model.run_structured.assert_not_awaited()


async def test_repair_preserves_user_report_and_rechecks_final_answer(monkeypatch):
    monkeypatch.setattr(settings, "LLM_JUDGE_CONTRACT_VERSION", "v1")
    monkeypatch.setattr(settings, "LLM_JUDGE_ENABLED", True)
    node, model, cache = component({"grounded": True, "fact_score": 1.,
        "has_unsupported_claim": False, "refuse_recommended": False, "reason": "synthetic"},
        AsyncMock(side_effect=["电源这一步已排除。", "你检查后仍然断线，请告诉我电源指示灯的状态。"] ))
    state = feedback()
    assert await node(state) == AgentNode.END
    assert "已排除" not in state.reply and state.review_result.passed
    assert node.generate.await_count == 2 and model.run_structured.await_count == 1
    assert "diagnostic_feedback" in answer_requirements(state)
    assert not cache.values


async def test_feedback_change_invalidates_review_before_publication(monkeypatch):
    monkeypatch.setattr(settings, "LLM_JUDGE_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_JUDGE_CONTRACT_VERSION", "v1")
    node, _, _ = component({"grounded": True, "fact_score": 1.,
        "has_unsupported_claim": False, "refuse_recommended": False, "reason": "synthetic"})
    state = feedback("电源正常，指示灯不亮。")
    state.reply = "你反馈电源正常。"
    assert await validate_candidate(node, state)
    state.user_message = "只是检查过，结果还不知道。"
    await publish_answer(node, state, AnswerDraft())
    assert not state.review_result.passed and "你反馈电源正常" not in state.reply


@pytest.mark.parametrize("error_type,known", [("conflict", True), ("permission", True),
    ("validation", True), ("dependency_unavailable", False), ("timeout", False)])
async def test_refund_rejection_uses_fixed_notice_without_echoing_error(error_type, known):
    state = AgentState(session_id="refund-error", user_id=101, user_message="确认", tool_calls=[
        ToolCall(tool_name="apply_refund", arguments={}, result={"success": False}, success=False,
                 error_type=error_type, error_message="arbitrary dependency message")])
    assert await verify_tool_result(state) == AgentNode.RESPONSE_GENERATION
    assert state.workflow_reply_digest and not state.need_human
    assert "arbitrary" not in state.reply
    assert ("未登记新申请" in state.reply) is known
    if not known:
        assert "暂时无法核实" in state.reply and "不要重复提交" in state.reply
