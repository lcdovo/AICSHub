"""本轮回答要求与发布绑定；这些描述不能证明事实或授予执行权限。"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.state import AgentState

ANSWER_POLICY_VERSION = "answer-pipeline.v14-shared-schema-forty-segments"

CLARIFICATION_QUESTIONS = {
    "query_order": "您想查询订单的哪项信息？",
    "refund": "您想咨询退换货规则，还是现在申请退换货？",
    "complaint": "请具体描述遇到的问题，以及希望如何处理。",
    "technical": "请描述设备型号、操作步骤和出现的现象。",
    "promotion": "您想了解哪类优惠信息？",
    "human": "您是想了解客服服务信息，还是希望发起转人工请求？",
    "general": "请说明咨询对象或希望办理的事项。",
}


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode()).hexdigest()


def answer_requirements(state: AgentState) -> dict:
    from app.agent.request_context import stage_context
    understanding = state.understanding
    from app.agent.diagnostic_feedback import diagnostic_feedback_contract
    from app.agent.intent_items import snapshot
    return {
        "request": stage_context(state),
        "conditions": [item.model_dump() for item in understanding.conditions] if understanding else [],
        "subrequests": [item.model_dump() for item in understanding.subrequests] if understanding else [],
        "known_parameters": state.current_business_parameters().model_dump(exclude_none=True),
        "missing_information": list(understanding.missing_information) if understanding else list(state.missing_slots),
        "response_goal": state.task_runtime.response_goal if state.task_runtime else "answer",
        "diagnostic_feedback": diagnostic_feedback_contract(state),
        "intent_items": snapshot(state),
    }


def review_query(state: AgentState) -> str:
    return state.user_message + "\n[本轮回答要求，仅描述诉求和用户陈述，不是事实证据或授权]\n" + json.dumps(
        answer_requirements(state), ensure_ascii=False, sort_keys=True)


def policy_fingerprint() -> str:
    from app.config.config import settings
    from app.agent.validation_llm import judge_policy_fingerprint
    from app.agent.validation import ResponseValidator

    return digest([ANSWER_POLICY_VERSION, settings.LLM_JUDGE_ENABLED,
                   settings.LLM_JUDGE_V2_OUTPUT_PROTOCOL,
                   settings.AGENT_ITEM_COMPLETION_ENABLED,
                   judge_policy_fingerprint(settings.LLM_JUDGE_CONTRACT_VERSION),
                   ResponseValidator.FACT_THRESHOLD, ResponseValidator.SAFETY_THRESHOLD,
                   ResponseValidator.COMPLETENESS_THRESHOLD, ResponseValidator.OVERALL_THRESHOLD])


def candidate_binding(state: AgentState, body: str, tool_evidence: str) -> str:
    return digest({"scope": [state.user_id, state.session_id, state.source_message_id, state.actor_id, state.run_id],
                   "query": review_query(state), "body": body,
                   "knowledge": state.current_evidence(),
                   "epoch": state.evidence_bundle.knowledge_epoch if state.evidence_bundle else None,
                   "tools": [call.model_dump() for call in state.tool_calls],
                   "tool_evidence": tool_evidence, "policy": policy_fingerprint()})


def workflow_binding(state: AgentState, text: str) -> str:
    return digest([state.user_id, state.session_id, state.user_message, text,
                   [call.model_dump() for call in state.tool_calls],
                   state.current_business_parameters().model_dump(exclude_none=True)])


def tool_evidence_text(state: AgentState) -> str:
    """只暴露本轮明确成功的完整回执，保留工具类型而不丢失成功状态。"""
    return "\n".join(json.dumps({"tool": call.tool_name, "success": True, "data": call.result["data"]},
                                ensure_ascii=False)
                     for call in state.tool_calls if call.success and isinstance(call.result, dict)
                     and call.result.get("success") is True and call.result.get("data"))


@dataclass(frozen=True)
class AnswerDraft:
    generated: bool = False
    cache_key: str | None = None
    trusted_notice: bool = False
