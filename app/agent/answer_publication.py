"""最终发布出口；核对审查绑定与发布快照后才引用、缓存及输出文本。"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.agent.answer_contracts import AnswerDraft, candidate_binding, tool_evidence_text
from app.agent.answer_review import answer_body, deterministic_issues, ensure_current_evidence
from app.agent.flow_contracts import FlowFlag
from app.agent.publication_guard import SAFE_PUBLICATION_REPLY
from app.agent.request_understanding import ReviewResult
from app.agent.state import AgentNode, AgentState

if TYPE_CHECKING:
    from app.agent.nodes.response_node import ResponseContext


async def publish_answer(context: ResponseContext, state: AgentState, draft: AnswerDraft) -> str:
    review = state.review_result
    issues = list(state.collected_info.get("validation", {}).get("deterministic_issues", []))
    if review is None:
        issues.append("missing_candidate_review")
    elif not review.passed:
        state.set_flow(answer_validated=False, rag_refused=True)
        if state.read_plan and review.status == "rejected":
            state.needs_clarification = True
        state.reply = (
            "本轮回答审查暂时不可用，尚未取得足够可靠的核验结果。请稍后重试，或回复“转人工”请求协助。"
            if review.status == "unknown" else
            "本轮回答未通过校验，暂时没有足够可靠的结论。请补充问题信息，或回复“转人工”请求协助。"
        )
    if review and review.passed:
        body = state.reply
        if not review.input_fingerprint or review.input_fingerprint != candidate_binding(
                state, body, tool_evidence_text(state)):
            issues.append("review_binding_changed")
        else:
            current = await ensure_current_evidence(context, state)
            if not current:
                state.review_result = ReviewResult(passed=False, status="unknown",
                    issue_codes=["knowledge_version_changed_before_publish"])
                state.set_flow(answer_validated=False, rag_refused=True)
            elif review.input_fingerprint != candidate_binding(state, state.reply, tool_evidence_text(state)):
                issues.append("review_binding_changed")

    judge = state.collected_info.get("llm_judge", {})
    from app.agent.intent_items import active, verify_review, finalize
    if active(state) and state.review_result and state.review_result.passed and state.task_runtime.item_reviews:
        _, item_issues = verify_review(state, answer_body(state.reply),
            [entry.model_dump() for entry in state.task_runtime.item_reviews])
        issues.extend(item_issues)
    elif active(state) and state.task.status == "completed":
        issues.append("item_review_missing_before_completion")
    if (state.review_result and state.review_result.passed and not issues
            and not draft.trusted_notice and state.current_evidence()
            and judge.get("response_behavior") in (None, "answer", "answer_with_limits")):
        evidence = state.current_evidence()
        if judge.get("schema_version") == 2:
            used = {tuple(ref[1:]) for claim in judge.get("claim_checks", [])
                    for ref in claim.get("evidence_ids", []) if ref[0] == "knowledge"}
            evidence = [item for item in evidence if (item.get("id"), item.get("version_id")) in used]
        reviewed_body = state.reply
        state.reply = (context._add_rag_citations(reviewed_body, evidence, limit=12) if active(state) else
                       context._add_rag_citations(reviewed_body, evidence))
        if answer_body(state.reply) != reviewed_body:
            issues.append("citation_render_changed_body")

    issues.extend(deterministic_issues(state.reply, state.current_evidence(),
        [call.result for call in state.tool_calls if isinstance(call.result, dict)]))
    from app.agent.diagnostic_feedback import diagnostic_feedback_contract, diagnostic_claim_issues
    issues.extend(diagnostic_claim_issues(answer_body(state.reply), diagnostic_feedback_contract(state)))
    from app.agent.task_context import repeated_diagnostic_issues
    issues.extend(repeated_diagnostic_issues(state, answer_body(state.reply)))
    issues = list(dict.fromkeys(issues))
    state.collected_info["publication_guard"] = {"passed": not issues, "issues": issues}
    if issues:
        state.reply = SAFE_PUBLICATION_REPLY
        state.set_flow(answer_validated=False, rag_refused=True)
        state.review_result = ReviewResult(passed=False, status="rejected", issue_codes=issues)

    cacheable = (draft.generated and draft.cache_key and state.flow_value(FlowFlag.VALIDATED)
            and state.review_result and state.review_result.passed
            and state.flow_value(FlowFlag.GROUNDED) and state.current_evidence()
            and not state.need_human and not state.flow_value(FlowFlag.REFUSED)
            and judge.get("cacheable", True) and not draft.trusted_notice
            and not state.current_generation_metadata().degraded)

    if state.task_runtime and not (state.review_result and state.review_result.passed):
        from app.agent.task_loop import progress
        progress(state, status="stopped", stop_reason="final_response_not_accepted", waiting_for=None)
    elif active(state):
        finalize(state)

    if (state.task_runtime and state.review_result and state.review_result.passed
            and state.task_runtime.response_goal == "diagnostic_feedback"):
        from app.agent.task_context import remember_task_turn
        remember_task_turn(state, "suggested_step", state.reply)

    state.messages.append({"role": "assistant", "content": state.reply, "intent": state.detected_intent,
        "tool_calls": [call.tool_name for call in state.tool_calls],
        "timestamp": datetime.now().isoformat(timespec="milliseconds")})
    from app.agent.stream_context import stream_event_queue
    queue = stream_event_queue.get()
    if queue is not None:
        for offset in range(0, len(state.reply), 64):
            await queue.put(("token", state.reply[offset:offset + 64]))
    # 缓存I/O不放在最后一次发布核对与公开文本之间，也不影响已接受回答。
    if cacheable:
        try:
            await context._answer_cache_set(draft.cache_key, state.reply)
        except Exception:
            state.collected_info["answer_cache_write"] = "unavailable"
    return AgentNode.END
