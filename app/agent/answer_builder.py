"""构建私有回答草稿；不审查、不写消息、不发布供应商token。"""
from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING
from app.agent.answer_contracts import AnswerDraft, workflow_binding
from app.agent.answer_review import ensure_current_evidence
from app.agent.flow_contracts import FlowFlag
from app.agent.state import IntentType
from app.agent.handoff_evidence import matching_handoff_receipt
from app.agent.request_events import is_deterministic_smalltalk
from app.services.user_memory import get_user_memory_service
from app.config.config import settings

if TYPE_CHECKING:
    from app.agent.nodes.response_node import ResponseContext
    from app.agent.state import AgentState

# 仅当前异步调用携带失败草稿；不进入持久状态、审查日志或缓存键。
repair_context: ContextVar[dict | None] = ContextVar("answer_repair_context", default=None)


def trusted_tool_reply(context: ResponseContext, state: AgentState, text: str) -> bool:
    """只信任固定回执字段模板；退款丢弃next_steps，任意正文仍须Judge。"""
    from app.agent.nodes.response_node import try_templated_tool_reply
    if not state.tool_calls:
        return False
    call = state.tool_calls[-1]
    if not call.success or not isinstance(call.result, dict) or call.result.get("success") is not True:
        return False
    data = call.result.get("data")
    if not isinstance(data, dict) or (data.get("next_steps") and call.tool_name != "apply_refund"):
        return False
    if state.understanding and (state.understanding.conditions or len(state.understanding.subrequests) > 1):
        return False
    if call.tool_name not in {"query_order", "apply_refund", "update_ticket_status", "escalate_to_human", "create_ticket"}:
        return False
    if call.tool_name == "create_ticket" and not (
            isinstance(data.get("ticket_id"), str) and data["ticket_id"].strip()
            and type(data.get("user_id")) is int and data["user_id"] == state.user_id
            and data.get("session_id") == state.session_id
            and data.get("status") in {"pending", "processing", "resolved", "closed", "escalated"}):
        return False
    if call.tool_name == "query_order" and data.get("status") not in {
            "待付款", "待发货", "已付款", "已发货", "已签收", "已完成", "已取消", "运输中"}:
        return False
    if call.tool_name == "apply_refund" and (
            data.get("is_simulated") is not True
            or data.get("status") not in {"pending_review", "approved", "rejected"}
            or data.get("type") not in {None, "refund", "return", "exchange"}):
        return False
    if call.tool_name == "update_ticket_status" and (
            not isinstance(data.get("ticket_id"), str) or not data["ticket_id"].strip()
            or data.get("new_status") not in {"pending", "processing", "resolved", "closed", "escalated"}):
        return False
    if call.tool_name == "escalate_to_human" and not matching_handoff_receipt(data, state.user_id, state.session_id):
        return False
    return text == try_templated_tool_reply(context, state)


async def build_draft(self: ResponseContext, state: AgentState) -> AnswerDraft:
    from app.agent.intent_items import active, snapshot
    if active(state) and snapshot(state).get("scope_valid") is False:
        from app.agent.task_loop import progress
        state.set_workflow_reply("请求上下文已变化，未继续处理或提交操作。请重新说明需求。")
        progress(state, status="stopped", stop_reason="item_scope_changed", waiting_for=None)
        return AnswerDraft(trusted_notice=True)
    if active(state) and state.task.status == "waiting_confirmation":
        # Generate all requested parts; the domain preview is still unsubmitted.
        state.reply = ""
        state.workflow_reply_digest = None
    is_shadow = (state.understanding_mode or settings.REQUEST_UNDERSTANDING_MODE) == "shadow"
    if is_shadow:
        # 影子只发布可信流程通知，不生成业务草稿或因审查失败转入写入型人工流程。
        from app.agent.request_understanding import SHADOW_NOTICE
        state.set_workflow_reply(SHADOW_NOTICE)
        state.need_human = False
        state.collected_info["reply_source"] = "shadow_notice"
    if state.need_human and not state.reply:
        # need_human是诉求/建议，不是接通事实。只按回执描述已登记状态。
        from app.services.ticket_creation import handoff_message
        receipt = next((call.result["data"] for call in reversed(state.tool_calls)
                        if call.tool_name == "escalate_to_human" and call.success
                        and isinstance(call.result, dict)
                        and matching_handoff_receipt(call.result.get("data"), state.user_id, state.session_id)), None)
        if receipt is not None:
            state.set_workflow_reply(handoff_message(receipt))
        elif state.flow_value(FlowFlag.HANDOFF_ATTEMPTED) or any(call.tool_name == "escalate_to_human" for call in state.tool_calls):
            state.set_workflow_reply("暂时无法确认转人工请求结果，请查看请求记录或联系支持。")
        else:
            templated = self._try_templated_tool_reply(state)
            if templated:
                state.reply = templated
                if trusted_tool_reply(self, state, templated):
                    state.set_workflow_reply(templated)
            else:
                state.set_workflow_reply("建议由人工进一步协助；当前尚未创建转人工请求，请回复“转人工”发起。")
    if state.flow_value(FlowFlag.SMALLTALK) and is_deterministic_smalltalk(state.user_message) and not state.reply:
        state.set_workflow_reply(self._get_default_reply(IntentType.GENERAL, state.user_message))

    await ensure_current_evidence(self, state)
    has_business_tool = any(tc.tool_name != "hybrid_search" for tc in state.tool_calls)
    grounded = bool(not is_shadow and state.flow_value(FlowFlag.GROUNDED) and state.current_evidence())
    answer_cache_key = None
    generated_by_llm = False
    if not state.reply and not state.task_runtime:
        templated = self._try_templated_tool_reply(state)
        if templated:
            state.reply = templated
            if trusted_tool_reply(self, state, templated):
                state.set_workflow_reply(templated)
            state.collected_info["reply_source"] = "tool_template"
    if not state.reply:
        if settings.USER_MEMORY_ENABLED:
            try:
                memory_factory = getattr(self, "memory_factory", get_user_memory_service)
                state.collected_info["user_memory"] = await memory_factory().get_user_profile(
                    user_id=state.user_id, query=state.user_message, session_id=state.session_id,
                )
            except Exception:
                state.collected_info["user_memory"] = {"memory_source": "degraded"}
        if grounded and not has_business_tool and not state.task_runtime:
            answer_cache_key = self._build_answer_cache_key(state)
            cached = await self._answer_cache_get(answer_cache_key)
            if cached is not None:
                state.reply = cached
                state.set_flow(answer_from_cache=True)
        if not state.reply:
            state.reply = await self._generate_response_with_llm(state)
            generated_by_llm = bool(state.reply) and state.collected_info.get('reply_source')!='item_evidence_extract'
            if not state.reply:
                if state.task_runtime:
                    state.set_workflow_reply("本轮暂时无法生成可靠的任务答复，未执行后续业务操作。请稍后继续。")
                    state.needs_clarification = True
                    from app.agent.task_loop import progress
                    progress(state, status="stopped", stop_reason="response_unavailable", waiting_for=None)
                else:
                    state.set_workflow_reply("本轮回答生成暂时不可用，请稍后重试或说明需要补充的问题。")

    return AnswerDraft(generated=generated_by_llm, cache_key=answer_cache_key,
                       trusted_notice=state.workflow_reply_digest == workflow_binding(state, state.reply))
