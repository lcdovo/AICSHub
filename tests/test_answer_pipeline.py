"""回答流水线的离线契约回归；手工Judge替身不证明模型语义识别质量。"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.agent.answer_contracts import AnswerDraft, answer_requirements
from app.agent.answer_publication import publish_answer
from app.agent.answer_review import validate_candidate
from app.agent.nodes.response_node import ResponseNode, ResponseServices
from app.agent.request_understanding import EvidenceBundle, RequestUnderstanding
from app.agent.state import AgentNode, AgentState
from app.agent.stream_context import stream_event_queue
from app.agent.validation import ResponseValidator
from app.agent.validation_llm import LLMRAGJudge
from app.config.config import settings
from tests.test_chat_turns import Cache


EVIDENCE = [{"id": "policy", "version_id": "v1", "content": "未拆封且购买七日内，可以申请退货。"}]
GOOD = EVIDENCE[0]["content"]
BAD = "购买七日内都可以退货。"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "LLM_JUDGE_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_JUDGE_CONTRACT_VERSION", "v2")
    monkeypatch.setattr(settings, "USER_MEMORY_ENABLED", False)


def verdict(text=GOOD, *, issue=None, clarify=False):
    return {"schema_version": 2, "answerability": "unknown" if clarify else "full",
        "response_behavior": "clarify" if clarify else "answer", "response_validity": "valid",
        "claims_complete": True,
        "claims": [] if clarify else [{"segment_id": 0, "text": text, "support": "supported",
            "evidence": [{"kind": "knowledge", "source_id": "policy", "version_id": "v1", "quote": GOOD}],
            "issues": [issue] if issue else []}],
        "nonfact_segments": [{"segment_id": 0, "kind": "clarification"}] if clarify else [],
        "response_issues": [issue] if clarify and issue else []}


def current(reply="", *, evidence=True):
    state = AgentState(session_id="synthetic", user_id=101, user_message="退货有什么条件？", reply=reply)
    if evidence:
        state.evidence_bundle = EvidenceBundle(items=deepcopy(EVIDENCE))
        state.set_flow(rag_grounded=True)
    return state


def component(raw=None, generate=None, knowledge=None):
    cache = Cache()
    model = SimpleNamespace(is_available=lambda: True, run_structured=AsyncMock(return_value=raw))
    knowledge = knowledge or SimpleNamespace(evidence_is_current=lambda _: True)
    node = ResponseNode(ResponseServices(lambda: None, ResponseValidator, lambda: LLMRAGJudge(model),
        lambda: cache, lambda: knowledge, lambda: None), generate=generate)
    return node, model, cache


@pytest.mark.parametrize("flag", ["clarify", "slots", "ungrounded", "handoff", "smalltalk"])
async def test_workflow_flags_do_not_exempt_untrusted_facts(flag):
    state = current(BAD)
    if flag == "clarify": state.needs_clarification = True
    elif flag == "slots": state.missing_slots = ["order_id"]
    elif flag == "ungrounded": state.set_flow(rag_grounded=False)
    elif flag == "handoff": state.set_flow(handoff_attempted=True)
    else: state.set_flow(smalltalk=True)
    node, model, cache = component(verdict(BAD, issue="condition_missing"))
    assert await node(state) == AgentNode.END
    assert BAD not in state.reply and not state.review_result.passed
    assert model.run_structured.await_count == 1 and not cache.values and not state.tool_calls
    assert "condition_missing" in state.review_result.issue_codes


async def test_safe_clarification_with_no_evidence_is_accepted_and_not_cached():
    reply = "请提供设备型号。"
    node, model, cache = component(verdict(clarify=True), AsyncMock(return_value=reply))
    state = current(evidence=False)
    state.needs_clarification = True
    assert await node(state) == AgentNode.END
    assert state.reply == reply and state.review_result.passed
    assert state.collected_info["validation"]["question_answerability"] == "unknown"
    assert model.run_structured.await_count == 1 and not cache.values


@pytest.mark.parametrize("issue", ["unnecessary_clarification", "unanswered_known_question"])
async def test_redundant_question_or_omitted_known_answer_is_rejected(issue):
    node, _, _ = component(verdict(clarify=True, issue=issue))
    state = current("请再提供订单号。")
    assert await node(state) == AgentNode.END
    assert not state.review_result.passed and issue in state.review_result.issue_codes


async def test_repair_receives_failure_and_only_revalidated_answer_is_published():
    observed = []
    state = current()
    async def generate(value):
        observed.append(node._build_llm_prompt(value))
        return BAD if len(observed) == 1 else GOOD
    node, model, cache = component(generate=generate)
    model.run_structured.side_effect = [verdict(BAD, issue="condition_missing"), verdict()]
    queue = asyncio.Queue()
    token = stream_event_queue.set(queue)
    try:
        assert await node(state) == AgentNode.END
    finally:
        stream_event_queue.reset(token)
    assert len(observed) == 2 and "condition_missing" in observed[1] and BAD in observed[1]
    assert "待修复草稿" not in node._build_llm_prompt(state)
    assert state.review_result.passed and model.run_structured.await_count == 2 and cache.values
    assert GOOD in state.reply and BAD not in str(state.messages)
    assert BAD not in str(state.collected_info)
    assert "".join(queue.get_nowait()[1] for _ in range(queue.qsize())) == state.reply


async def test_judge_unknown_does_not_regenerate_or_create_handoff():
    generate = AsyncMock(return_value=GOOD)
    node, model, cache = component(None, generate)
    state = current()
    assert await node(state) == AgentNode.END
    assert "审查暂时不可用" in state.reply and state.review_result.status == "unknown"
    assert generate.await_count == model.run_structured.await_count == 1
    assert not state.need_human and not state.tool_calls and not cache.values


async def test_repair_is_bounded_and_never_creates_handoff():
    generate = AsyncMock(return_value=BAD)
    node, model, cache = component(verdict(BAD, issue="condition_missing"), generate)
    state = current()
    assert await node(state) == AgentNode.END
    assert not state.review_result.passed and generate.await_count == model.run_structured.await_count == 2
    assert not state.need_human and not state.tool_calls and not cache.values


@pytest.mark.parametrize("change", ["body", "query", "evidence", "tool", "policy", "condition", "scope"])
async def test_input_changes_after_review_are_blocked_before_publication(change, monkeypatch):
    node, _, cache = component(verdict())
    state = current(GOOD)
    assert await validate_candidate(node, state)
    if change == "body": state.reply += "新增承诺。"
    elif change == "query": state.user_message = "不同问题"
    elif change == "evidence": state.evidence_bundle.items[0]["version_id"] = "v2"
    elif change == "tool": state.add_tool_call("query_order", {}, {"success": True, "data": {"status": "pending"}})
    elif change == "policy": monkeypatch.setattr(settings, "LLM_JUDGE_ENABLED", False)
    elif change == "scope": state.user_id = 202
    else: state.missing_slots = ["order_id"]
    assert await publish_answer(node, state, AnswerDraft(generated=True, cache_key="synthetic")) == AgentNode.END
    assert not state.review_result.passed and "参考来源" not in state.reply and not cache.values
    assert "review_binding_changed" in state.collected_info["publication_guard"]["issues"]


async def test_mutation_while_judge_is_pending_is_not_authorized_by_snapshot_verdict():
    node, model, _ = component()
    state = current(GOOD)
    def respond(*args, **kwargs):
        state.reply = BAD
        return verdict()
    model.run_structured.side_effect = respond
    assert not await validate_candidate(node, state)
    assert state.review_result.status == "unknown" and "review_input_changed" in state.review_result.issue_codes


async def test_knowledge_unpublished_after_review_prevents_citations_and_cache():
    knowledge = SimpleNamespace(evidence_is_current=Mock(side_effect=[True, True, False]))
    node, _, cache = component(verdict(), AsyncMock(return_value=GOOD), knowledge)
    state = current()
    assert await node(state) == AgentNode.END
    assert "知识版本已变化" in state.reply and "参考来源" not in state.reply
    assert not state.review_result.passed and not cache.values


async def test_cached_answers_are_reviewed_and_old_policy_namespace_is_not_reused():
    generate = AsyncMock(return_value=GOOD)
    node, model, cache = component(verdict(), generate)
    first = current()
    await node(first)
    assert next(iter(cache.values)).startswith("validated-v4:")
    model.run_structured.return_value = verdict(GOOD, issue="condition_missing")
    second = current()
    await node(second)
    assert generate.await_count == 1 and model.run_structured.await_count == 2
    assert GOOD not in second.reply and not second.review_result.passed


async def test_server_notice_requires_exact_binding_and_does_not_survive_new_turn():
    node, model, _ = component(None)
    state = current(evidence=False)
    state.set_workflow_reply("请补充订单号。")
    await node(state)
    assert state.reply == "请补充订单号。" and state.review_result.passed
    model.run_structured.assert_not_awaited()
    next_turn = state.for_next_turn(101, "查询另一订单")
    assert next_turn.workflow_reply_digest is None and next_turn.review_result is None
    state = current(evidence=False)
    state.set_workflow_reply("请补充订单号。")
    state.reply += "此型号支持999W。"
    await node(state)
    assert "999W" not in state.reply and model.run_structured.await_count == 1


async def test_clarification_node_does_not_append_unreviewed_assistant_message():
    from app.agent.nodes.intent_node import clarify_request
    state = current(evidence=False)
    await clarify_request(SimpleNamespace(recognizer=SimpleNamespace(
        get_clarification_question=lambda _: "请提供设备型号。")), state)
    assert not state.messages
    node, _, _ = component(verdict(clarify=True))
    await node(state)
    assert [item["content"] for item in state.messages] == ["请提供设备型号。"]


def test_requirements_preserve_conditions_subrequests_and_known_input():
    state = current()
    state.understanding = RequestUnderstanding(topic="refund", request_type="apply", entities={},
        conditions=[{"check": "order_not_shipped", "source": "current_message"}],
        subrequests=[{"topic": "query_order", "request_type": "query"}, {"topic": "refund", "request_type": "apply"}],
        service_preference="bot", emotion="neutral", clarification=None,
        retrieval_query="没发货才申请退款", entity_source="none")
    requirements = answer_requirements(state)
    assert requirements["request"]["current_message"] == state.user_message
    assert requirements["conditions"][0]["check"] == "order_not_shipped"
    assert len(requirements["subrequests"]) == 2


async def test_deterministic_review_failure_is_unknown_without_retry_or_detail_leak():
    generate = AsyncMock(return_value=GOOD)
    node, model, cache = component(verdict(), generate)
    failing = SimpleNamespace(validate=Mock(side_effect=RuntimeError("synthetic-private-detail")))
    node.services = ResponseServices(lambda: None, lambda: failing, node.services.judge_factory,
        node.services.cache_factory, node.services.knowledge_factory, lambda: None)
    state = current()
    assert await node(state) == AgentNode.END
    assert state.review_result.status == "unknown" and generate.await_count == 1
    assert "synthetic-private-detail" not in str(state.collected_info) + state.reply
    model.run_structured.assert_not_awaited()
    assert not cache.values


async def test_citation_renderer_cannot_modify_reviewed_body():
    node, _, cache = component(verdict(), AsyncMock(return_value=GOOD))
    node._add_rag_citations = lambda body, evidence: body + "可以终身换新。"
    state = current()
    await node(state)
    assert not state.review_result.passed and "终身换新" not in state.reply and not cache.values
    assert "citation_render_changed_body" in state.collected_info["publication_guard"]["issues"]


async def test_v2_cites_only_referenced_knowledge():
    node, _, _ = component(verdict(), AsyncMock(return_value=GOOD))
    state = current()
    state.evidence_bundle.items.append({"id": "unrelated", "version_id": "v1", "content": "无关的其他产品资料。"})
    await node(state)
    assert state.review_result.passed and "参考来源" in state.reply and "无关的其他产品资料" not in state.reply


@pytest.mark.parametrize("flag", [False, None, "true"])
def test_failed_or_untyped_receipts_are_not_judge_evidence(flag):
    from app.agent.nodes.response_node import tool_evidence_text
    state = current()
    state.add_tool_call("query_order", {}, {"success": flag, "data": {"status": "已发货"}}, True)
    assert tool_evidence_text(state) == ""


async def test_cache_failure_does_not_prevent_accepted_answer_publication():
    node, _, cache = component(verdict(), AsyncMock(return_value=GOOD))
    cache.aset = AsyncMock(side_effect=OSError("synthetic-private-detail"))
    state = current()
    assert await node(state) == AgentNode.END
    assert state.review_result.passed and GOOD in state.reply
    assert state.collected_info["answer_cache_write"] == "unavailable"
    assert "synthetic-private-detail" not in str(state.collected_info)


async def test_fixed_ticket_receipt_does_not_publish_arbitrary_tool_message():
    node, model, _ = component(None)
    state = current(evidence=False)
    state.detected_intent = "complaint"
    state.add_tool_call("create_ticket", {}, {"success": True, "data": {
        "ticket_id": "synthetic-ticket", "status": "pending", "user_id": 101, "session_id": state.session_id,
        "message": "此设备支持999W。"}}, True)
    assert await node(state) == AgentNode.END
    assert "synthetic-ticket" in state.reply and "999W" not in state.reply and state.review_result.passed
    model.run_structured.assert_not_awaited()


async def test_fixed_refund_receipt_discards_arbitrary_next_steps():
    node, model, _ = component(None)
    state = current(evidence=False)
    state.user_message, state.detected_intent = "确认", "refund"
    state.add_tool_call("apply_refund", {}, {"success": True, "data": {
        "refund_id": "synthetic-refund", "status": "pending_review", "is_simulated": True,
        "amount": "0.10", "status_label": "已到账", "type_label": "999W万能设备",
        "next_steps": ["此设备支持999W。"]}}, True)
    assert await node(state) == AgentNode.END
    assert "synthetic-refund" in state.reply and "999W" not in state.reply and state.review_result.passed
    assert "尚未执行退款支付" in state.reply
    assert "已到账" not in state.reply and "待审核" in state.reply
    model.run_structured.assert_not_awaited()
