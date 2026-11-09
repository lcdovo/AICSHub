"""确定性检查、独立Judge和程序判决；不生成回复、不发布、不执行业务。"""
from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from app.agent.answer_contracts import candidate_binding, review_query, workflow_binding, tool_evidence_text
from app.agent.request_understanding import ReviewResult
from app.agent.validation_llm import assess_judgement, unknown_judgement
from app.config.config import settings

if TYPE_CHECKING:
    from app.agent.nodes.response_node import ResponseContext
    from app.agent.state import AgentState

VERSION_CHANGED_REPLY = "知识版本已变化，请重新提问以读取最新发布内容。"
VERSION_UNKNOWN_REPLY = "本轮知识版本暂时无法核实，请稍后重试。"


def answer_body(text: str) -> str:
    return re.split(r"\n+\s*参考来源[：:]", text, maxsplit=1)[0].rstrip()


async def current_knowledge_evidence(context: ResponseContext, items: list[dict]) -> bool:
    factory = getattr(context, "knowledge_factory", None)
    if factory is None:
        from app.services.knowledge_base import get_knowledge_base
        factory = get_knowledge_base
    return await asyncio.to_thread(lambda: factory().evidence_is_current(items))


async def ensure_current_evidence(context: ResponseContext, state: AgentState) -> bool:
    items = state.current_evidence()
    if not items or not any(item.get("version_id") for item in items):
        return True
    try:
        current = await current_knowledge_evidence(context, items)
    except Exception:
        state.invalidate_evidence("knowledge_version_unavailable")
        state.set_workflow_reply(VERSION_UNKNOWN_REPLY)
        return False
    if not current:
        state.invalidate_evidence("knowledge_version_changed")
        state.set_workflow_reply(VERSION_CHANGED_REPLY)
    return current


def deterministic_issues(response: str, evidence: list[dict], tool_results: list[dict]) -> list[str]:
    from app.agent.citation_guard import citation_issues
    from app.agent.critical_facts import critical_fact_issues
    from app.agent.publication_guard import publication_issues
    return list(dict.fromkeys(publication_issues(response) + citation_issues(response, evidence)
                             + [item["type"] for item in critical_fact_issues(response, tool_results)]))


async def validate_candidate(context: ResponseContext, state: AgentState, *, trusted_notice: bool = False) -> bool:
    """每次草稿独立审查；模型与发布端均绑定同一输入快照。"""
    if not await ensure_current_evidence(context, state):
        trusted_notice = True
    state.reply = answer_body(state.reply)
    snapshot = state.model_copy(deep=True)
    if state.task_runtime:
        state.task_runtime.item_reviews = []
    body = snapshot.reply
    tools = tool_evidence_text(snapshot)
    binding = candidate_binding(snapshot, body, tools)
    state.collected_info.pop("llm_judge", None)
    state.collected_info.pop("inline_self_check", None)
    notice_bound = trusted_notice and snapshot.workflow_reply_digest == workflow_binding(snapshot, body)
    try:
        if notice_bound:
            # 固定通知只走下方不可替换的确定性底线，不调用任意注入的语义评分器。
            from app.agent.validation import ValidationResult
            validation = ValidationResult()
        else:
            validation = context.validator.validate(
                response=body, user_query=snapshot.user_message, intent=snapshot.detected_intent,
                tool_results=[call.result for call in snapshot.tool_calls if isinstance(call.result, dict)])
        report = validation.to_dict()
        if type(validation.passed) is not bool or not isinstance(report.get("issues"), list):
            raise ValueError("invalid_deterministic_review")
        passed = validation.passed
        status = "accepted" if passed else "rejected"
    except Exception:
        passed, status = False, "unknown"
        report = {"issues": [{"type": "deterministic_review_unavailable", "severity": "error"}]}
    codes = deterministic_issues(body, snapshot.current_evidence(),
                                 [call.result for call in snapshot.tool_calls if isinstance(call.result, dict)])
    from app.agent.diagnostic_feedback import diagnostic_feedback_contract, diagnostic_claim_issues
    codes.extend(diagnostic_claim_issues(body, diagnostic_feedback_contract(snapshot)))
    from app.agent.task_context import repeated_diagnostic_issues
    codes.extend(repeated_diagnostic_issues(snapshot, body))
    if codes:
        passed, status = False, "rejected"
    report["deterministic_issues"] = codes
    existing = {item.get("type") for item in report["issues"]}
    report["issues"].extend({"type": code, "severity": "critical"} for code in codes if code not in existing)
    # 只有生产者明确标记且文本未改变的服务端通知可以走确定性通道。
    # needs_clarification/missing_slots/handoff等业务标记均不能免除语义审查。
    if passed and not notice_bound:
        query = review_query(snapshot)
        from app.agent.intent_items import REVIEW_CONTEXT, snapshot as item_snapshot, verify_review, active
        item_context = item_snapshot(snapshot)
        context_token = REVIEW_CONTEXT.set(item_context)
        try:
            judgement = await context.llm_judge.judge(response=body, user_query=query,
                kb_context=snapshot.current_evidence(), tool_evidence=tools)
        except Exception:
            judgement = unknown_judgement("judge_dependency_failed")
        finally:
            REVIEW_CONTEXT.reset(context_token)
        raw_items = judgement.get("item_checks") if isinstance(judgement, dict) else None
        diagnostic = judgement.get("diagnostic") if isinstance(judgement, dict) else None
        judgement = assess_judgement(judgement, response=body, kb_context=snapshot.current_evidence(),
            tool_evidence=tools, contract_version=settings.LLM_JUDGE_CONTRACT_VERSION, user_query=query,item_context=item_context)
        if isinstance(diagnostic, dict):
            judgement["diagnostic"] = diagnostic
        if active(snapshot):
            item_reviews, item_issues = verify_review(snapshot, body, raw_items)
            judgement["item_checks"] = raw_items
            if item_issues:
                unknown = bool({"item_review_invalid", "item_scope_changed"}.intersection(item_issues))
                judgement.update(publish_allowed=False, cacheable=False,
                    support="unknown" if unknown else "partial", response_validity="unknown" if unknown else "invalid")
                report["issues"].extend({"type": code, "severity": "error"} for code in item_issues)
            elif judgement["publish_allowed"]:
                state.task_runtime.item_reviews = item_reviews
        state.collected_info["llm_judge"] = {key: value for key, value in judgement.items() if key != "_v2_verdict"}
        passed = judgement["publish_allowed"]
        status = "accepted" if passed else ("unknown" if judgement["response_validity"] == "unknown" else "rejected")
        report.update(judge_passed=passed, evidence_score=judgement["fact_score"],
                      evidence_support=judgement.get("evidence_support", judgement["support"]),
                      question_answerability=judgement.get("answerability", "not_assessed"),
                      candidate_validity=judgement["response_validity"])
        if state.evidence_bundle is not None:
            state.evidence_bundle.support = report["evidence_support"]
        if not passed:
            report["issues"].append({"type": "evidence_" + judgement["support"], "severity": "error"})
    if binding != candidate_binding(state, answer_body(state.reply), tool_evidence_text(state)):
        passed, status = False, "unknown"
        report["issues"].append({"type": "review_input_changed", "severity": "critical"})
    issue_codes = sorted({item["type"] for item in report["issues"] if not passed and item.get("severity") != "info"})
    judge = state.collected_info.get("llm_judge", {})
    issue_codes = sorted(set(issue_codes + judge.get("response_issues", [])
                            + [code for claim in judge.get("claim_checks", []) for code in claim.get("issues", [])]))
    state.review_result = ReviewResult(passed=passed, status=status, input_fingerprint=binding,
        evidence_version=snapshot.evidence_bundle.fingerprint() if snapshot.evidence_bundle else None,
        issue_codes=issue_codes, repairable=status == "rejected")
    report.update(passed=passed, review_status=status)
    state.collected_info["validation"] = report
    state.set_flow(answer_validated=passed, rag_refused=not passed)
    return passed
