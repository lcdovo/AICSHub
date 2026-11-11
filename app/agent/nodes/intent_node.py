"""意图理解链路节点：意图识别、路由决策、槽位补齐、澄清。

自 graph.py 拆分迁移（原 AgentGraph._node_intent_recognition 等），逻辑保持原样：
路由决策直接修改 state 后返回 next_node，图由 graph.py 统一装配。
"""
from app.agent.flow_contracts import FlowFlag


import logging
from typing import Any, Dict, List, Optional, Callable, Protocol
from dataclasses import dataclass
from app.agent.intent import IntentResult
from app.agent.routing_policy import DEFAULT_ROUTING_POLICY, RoutingPolicy, explicit_action, TOPIC_POLICIES
from app.agent.request_context import (
    build_request_context, RefinedIntent, ambiguous_order_question, order_mentions, is_slot_reply,
)
from app.agent.request_events import is_deterministic_smalltalk

from app.agent.state import AgentState, AgentNode, IntentType
from app.config.config import settings

logger = logging.getLogger(__name__)


class Recognizer(Protocol):
    def recognize(self, *, message: str, context: list[dict], history_intent: str | None,
                  llm_primary: bool) -> IntentResult: ...
    def get_clarification_question(self, intent: str) -> str: ...


class UnderstandingModel(Protocol):
    async def run_structured(self, *args, **kwargs) -> Any: ...
    async def classify_intent(self, message: str, context: list[dict]) -> dict | None: ...
    def is_available(self) -> bool: ...


class ActionCancellation(Protocol):
    async def cancel(self, actor_id: int, user_id: int, action_id: str) -> dict: ...


@dataclass(frozen=True)
class IntentDependencies:
    recognizer: Recognizer
    model_factory: Callable[[], UnderstandingModel]
    confirmed: Callable[[AgentState], bool]
    cancelled: Callable[[AgentState], bool]
    smalltalk: Callable[[str], bool]
    escalation: Callable[[list[str], str], list[str] | None]
    actions: ActionCancellation


@dataclass(frozen=True)
class RoutingDependencies:
    extract_info: Callable[[str], dict]
    policy: RoutingPolicy = DEFAULT_ROUTING_POLICY


@dataclass(frozen=True)
class ClarificationDependencies:
    recognizer: Recognizer


@dataclass(frozen=True)
class IntentNode:
    dependencies: IntentDependencies

    async def __call__(self, state: AgentState) -> str:
        return await recognize_request(self.dependencies, state)


@dataclass(frozen=True)
class RoutingNode:
    dependencies: RoutingDependencies

    async def __call__(self, state: AgentState) -> str:
        return await route_request(self.dependencies, state)


@dataclass(frozen=True)
class ClarificationNode:
    dependencies: ClarificationDependencies

    async def __call__(self, state: AgentState) -> str:
        return await clarify_request(self.dependencies, state)


async def recognize_request(dependencies: "IntentDependencies", state: AgentState) -> str:
    mode = state.understanding_mode or settings.REQUEST_UNDERSTANDING_MODE
    if mode == "shadow":
        from app.agent.request_understanding import understand, SHADOW_NOTICE

        result = await understand(dependencies.model_factory(), state)
        state.collected_info["shadow_understanding"] = (
            result.model_dump() if result else {"status": "unavailable"}
        )
        state.set_workflow_reply(SHADOW_NOTICE)
        return AgentNode.RESPONSE_GENERATION
    preview = state.refund_preview_data()
    state.current_refund_action()
    if state.refund_preview_error or state.refund_action_error:
        state.set_workflow_reply("退款预览无效或已变化，请重新核对当前操作。")
        state.needs_clarification = True
        return AgentNode.RESPONSE_GENERATION
    previous_reply = next((str(item.get("content", "")) for item in reversed(state.messages)
                           if item.get("role") == "assistant"), "")
    if (not preview and dependencies.confirmed(state) and (
            state.detected_intent == IntentType.REFUND
            or "原预览已撤销" in previous_reply
            or any(word in state.user_message for word in ("退款", "退货", "换货")))):
        state.set_workflow_reply("当前没有有效的待确认退款预览，本轮未提交。请重新说明需求并生成预览，再明确确认。")
        state.needs_clarification = True
        return AgentNode.RESPONSE_GENERATION
    if preview and dependencies.cancelled(state):
        action_id = preview.get("action_id")
        if action_id:
            await dependencies.actions.cancel(
                state.actor_id or state.user_id, state.user_id, action_id
            )
        state.detected_intent = IntentType.REFUND
        state.intent_confidence = 1.0
        state.needs_clarification = False
        state.pending_confirmations = []
        state.set_refund_preview(None)
        state.collected_info.pop("confirm_refund", None)
        state.record_refund_action(None)
        state.set_workflow_reply("已取消本次退款申请，未执行任何退款操作。")
        return AgentNode.RESPONSE_GENERATION

    # 确认回复通常只有两个字，优先沿用待确认动作的意图，避免被识别为 general。
    if preview and dependencies.confirmed(state):
        state.detected_intent = IntentType.REFUND
        state.intent_confidence = 1.0
        state.needs_clarification = False
        return AgentNode.ROUTE_DECISION

    if preview:
        from app.agent.refund_authorization import refund_confirmation_issue
        action = state.current_refund_action()
        issue = refund_confirmation_issue(state.user_message, preview.get("type", ""))
        if (issue and not order_mentions(state.user_message) and action is not None
                and action.is_pending() and action.matches(state.current_refund_preview())):
            # Conditions attached to a confirmation cannot be forgotten by a later
            # plain "confirm". Retire the old draft instead of silently changing it.
            import re
            conditional = bool(re.match(r"^(?:如果|若|要是|只有)", state.user_message.strip()))
            if conditional:
                await dependencies.actions.cancel(state.actor_id or state.user_id, state.user_id, preview["action_id"])
                state.pending_confirmations = []
                state.set_refund_preview(None)
                state.record_refund_action(None)
                state.collected_info.pop("confirm_refund", None)
                if state.task:
                    from app.agent.task_loop import progress
                    progress(state, status="cancelled", waiting_for=None, stop_reason="confirmation_conditions_changed")
                reply = "本轮确认带有新增条件，原预览已撤销，本轮未提交。请明确办理条件并重新预览，再进行确认。"
            else:
                prefix = "确认中的业务类型与当前预览不一致。" if issue == "confirmation_type_mismatch" else "本轮表述不构成对当前预览的明确确认。"
                reply = prefix + "本轮未提交，当前预览仍待确认；如需修改操作请重新说明并预览，取消请明确回复“取消”。"
            state.detected_intent = IntentType.REFUND
            state.need_human = False
            state.needs_clarification = True
            state.set_workflow_reply(reply)
            return AgentNode.RESPONSE_GENERATION

    if is_deterministic_smalltalk(state.user_message):
        state.detected_intent = IntentType.GENERAL
        state.needs_clarification = False
        state.missing_slots = []
        state.need_human = False
        state.set_flow(smalltalk=True)
        state.collected_info["intent_source"] = "deterministic_event"
        return AgentNode.RESPONSE_GENERATION

    if mode == "model":
        from app.agent.request_understanding import understand, route_understanding

        result = await understand(dependencies.model_factory(), state)
        if result:
            from app.agent.task_loop import enter_task
            return enter_task(state, route_understanding(state, result))
        code = (state.collected_info.get("understanding_failure") or {}).get("code")
        # 模型理解不可用时，安全的只读请求仍可走规则路由；写入型动作继续
        # fail-closed，避免在模型不可用时误提交退款或投诉。供应商连接异常
        # 可能没有可归一化的 failure_code，此时同样按只读/写入边界处理。
        if code in {
            "daily_model_budget_exhausted",
            "request_model_budget_exceeded",
            "run_model_budget_exhausted",
            "provider_timeout",
            "model_concurrency_exhausted",
        } or code is None:
            fallback = await recognize_rule_request(
                dependencies, state, allow_llm_fallback=False
            )
            write_intent = state.detected_intent in {IntentType.REFUND, IntentType.COMPLAINT}
            write_request = write_intent and explicit_action(state.detected_intent, state.user_message)
            compound_request = state.task_loop_enabled and any(marker in state.user_message for marker in (
                "并且", "同时", "另外", "顺便", "再告诉", "如果", "若", "要是"
            ))
            if not write_request and not compound_request:
                state.collected_info["intent_source"] = "rules_after_model_failure"
                return fallback
        notices = {
            "daily_model_budget_exhausted": "当前账号今日模型额度不足，本轮未执行后续操作。请稍后重试或联系管理员调整额度。",
            "request_model_budget_exceeded": "本轮模型预算不足，未执行后续操作。请缩小请求范围或联系管理员。",
            "run_model_budget_exhausted": "本轮模型调用次数已达到上限，未执行后续操作。请缩小请求范围。",
            "model_concurrency_exhausted": "当前模型服务繁忙，本轮未执行后续操作。请稍后重试。",
            "provider_timeout": "本轮模型响应超时，未执行后续操作。请稍后重试。",
            "structured_output_invalid": "本轮模型输出格式未通过校验，未执行后续操作。请稍后重试。",
        }
        state.set_workflow_reply(notices.get(code, "本轮请求理解暂时不可用，请明确说明咨询对象或希望办理的操作。"))
        state.needs_clarification = True
        return AgentNode.RESPONSE_GENERATION

    return await recognize_rule_request(dependencies, state)


async def recognize_rule_request(
    dependencies: "IntentDependencies", state: AgentState, *, allow_llm_fallback: bool = True
) -> str:
    """独立保留的旧规则模式；候选模型路径不进入此函数或旧分类兜底。"""
    if question := ambiguous_order_question(state.user_message):
        state.set_business_parameters(order_id=None)
        state.reply, state.needs_clarification = question, True
        return AgentNode.RESPONSE_GENERATION
    context = build_request_context(state)
    if (context.known_order is None and not context.history
            and explicit_action("refund", state.user_message)):
        # 独立的新办理请求不能借默认保留的历史订单选择办理对象。
        if not order_mentions(state.user_message):
            state.set_business_parameters(order_id=None)
    intent_result = dependencies.recognizer.recognize(
        message=state.user_message,
        context=context.classifier_history(),
        history_intent=state.detected_intent
        if context.history or context.known_order
        else None,
        llm_primary=settings.LLM_INTENT_PRIMARY if allow_llm_fallback else False,
    )
    if (state.missing_slots and context.unfinished_topic and is_slot_reply(state.user_message)
            and not dependencies.smalltalk(state.user_message)):
        intent_result = IntentResult(context.unfinished_topic, 0.0)

    # 意图判定的唯一权威入口是 intent.py 的规则识别：
    #  - LLM 主判路径（LLM_INTENT_PRIMARY=true）：硬性优先意图（转人工/投诉强情绪/
    #    退款动作/订单号识别）由规则直接拍板，其余交 LLM 结构化分类做语义泛化；
    #  - 原规则优先路径：LLM 仅对低置信输入二次确认（可回退）。
    state.collected_info["intent_source"] = "rules"

    # 寒暄拦截优先于澄清与 LLM 兜底：高频寒暄语（你好/谢谢/再见等）已由
    # 规则模板覆盖，强制解除 ≤5 字触发的澄清标记，并且不再调用分类模型二次确认，
    # 由路由直接送入响应生成节点返回固定文案（每轮重算布尔值，防止跨轮泄漏）。
    is_smalltalk = intent_result.intent == IntentType.GENERAL and dependencies.smalltalk(
        state.user_message
    )
    state.set_flow(smalltalk=is_smalltalk)
    if is_smalltalk:
        intent_result.needs_clarification = False

    # ⑤ 分层阈值 + ④ 澄清态续答：仅当规则判定"需要 LLM 复审"时才调用模型兜底。
    #  - 规则已明显自信（top1 领先 top2 ≥0.5 且置信度达标）→ 直接信任规则；
    #  - 上一轮已处于澄清态（state.needs_clarification=True）→ 本轮用户补充
    #    信息（订单号/短答复）时由 CLARIFICATION/槽位节点接着走，不再调 LLM 兜底，
    #    避免多轮追问重复耗一次模型调用。
    is_continuation_reply = (
        state.needs_clarification
        and len(state.user_message.strip()) <= 20
        and intent_result.needs_clarification is False
    )
    if allow_llm_fallback and (
        intent_result.needs_llm_fallback
        and not is_smalltalk
        and not is_continuation_reply
    ):
        try:
            refined = await dependencies.model_factory().classify_intent(
                context.current_message, context=context.classifier_history()
            )
            if refined is not None:
                parsed = RefinedIntent.model_validate(refined)
                if (parsed.intent in {"refund", "complaint", "human"}
                        and not explicit_action(parsed.intent, context.current_message)
                        and DEFAULT_ROUTING_POLICY.request_kind(parsed.intent, context.current_message) != "inquiry"):
                    raise ValueError("unconfirmed_action_topic")
                # 自报置信度只保留为观察；合法性和冲突保护决定是否使用结果。
                intent_result.intent = parsed.intent
                intent_result.confidence = parsed.confidence
                intent_result.needs_clarification = parsed.need_clarification
                state.collected_info["intent_source"] = "llm_validated"
        except Exception:
            state.set_workflow_reply("本轮请求理解暂时无法核实，请明确您是咨询、查询还是希望办理业务。")
            state.needs_clarification = True
            return AgentNode.RESPONSE_GENERATION

    state.detected_intent = intent_result.intent
    state.intent_confidence = intent_result.confidence
    state.needs_clarification = intent_result.needs_clarification
    state.user_emotion = getattr(intent_result, "emotion", "neutral")

    if intent_result.intent == IntentType.HUMAN and explicit_action("human", state.user_message):
        state.need_human = True
        if not state.human_reason:
            state.human_reason = "用户请求转接人工客服"

    # 意图序列分析：检测升级模式（如 query_order → refund → complaint）
    intent_history = state.collected_info.get("intent_history", [])
    if intent_history:
        escalation = dependencies.escalation(
            intent_history, intent_result.intent
        )
        if escalation:
            # 历史升级只提供建议，不把情绪或意图序列变成写入型人工请求。
            state.collected_info["escalation_suggestion"] = list(escalation)

    # 更新意图序列
    updated_history = intent_history + [intent_result.intent]
    state.collected_info["intent_history"] = updated_history[-10:]  # 保留最近10轮

    if intent_result.needs_clarification:
        return AgentNode.CLARIFICATION

    return AgentNode.ROUTE_DECISION


def detect_escalation(
    intent_history: List[str], current_intent: str
) -> Optional[List[str]]:
    """检测意图升级模式，返回完整升级路径（含当前意图），无升级返回 None。
    升级路径定义：
    - query_order → refund → complaint：查询订单后申请退款再投诉
    - technical → refund → complaint：技术问题转退款再投诉
    - query_order → human：查单后直接转人工
    - refund → complaint：退款后投诉
    - complaint → human：投诉后转人工
    """
    escalation_chains = [
        (["query_order", "refund", "complaint"], "订单→退款→投诉"),
        (["technical", "refund", "complaint"], "技术→退款→投诉"),
        (["query_order", "human"], "订单→人工"),
        (["refund", "complaint"], "退款→投诉"),
        (["complaint", "human"], "投诉→人工"),
    ]

    recent = intent_history[-2:]
    full_sequence = recent + [current_intent]

    for chain, label in escalation_chains:
        if len(full_sequence) >= len(chain):
            tail = full_sequence[-len(chain) :]
            if tail == chain:
                return tail
    return None


async def route_request(dependencies: "RoutingDependencies", state: AgentState) -> str:
    """根据意图和已收集槽位选择业务分支。"""
    state.collected_info["user_id"] = state.user_id
    state.collected_info["session_id"] = state.session_id
    if state.understanding is not None:
        from app.agent.request_understanding import validate_semantics, execution_request_type, execution_missing_information
        try:
            validate_semantics(state.understanding, state)
        except ValueError:
            state.set_workflow_reply("本轮理解或对象来源无法核实，请重新说明请求。")
            state.needs_clarification = True
            return AgentNode.RESPONSE_GENERATION
        request_type = execution_request_type(state.understanding)
        # 对投诉政策问法保留规则优先，避免模型仅因出现“投诉”就误判为建单申请。
        if (state.detected_intent == IntentType.COMPLAINT
                and dependencies.policy.request_kind(IntentType.COMPLAINT, state.user_message) == "inquiry"):
            request_type = "inquiry"
        decision = dependencies.policy.decide_structured(
            topic=state.detected_intent, request_type=request_type,
            parameters=state.current_business_parameters().model_dump(exclude_none=True),
            missing_information=execution_missing_information(state.understanding),
        )
        state.route_reason = decision.reason
        state.missing_slots = list(decision.missing_slots)
        if decision.reply is not None:
            state.set_workflow_reply(decision.reply)
            state.needs_clarification = decision.needs_clarification
        return decision.next_node
    if state.understanding is None and (question := ambiguous_order_question(state.user_message)):
        state.set_business_parameters(order_id=None)
        state.reply, state.needs_clarification = question, True
        state.route_reason = "ambiguous_order->clarify"
        return AgentNode.RESPONSE_GENERATION
    extracted = dependencies.extract_info(state.user_message)
    continuing_reason = (state.detected_intent == IntentType.REFUND and "reason" in state.missing_slots
                         and is_slot_reply(state.user_message))
    if continuing_reason:
        extracted["reason"] = state.user_message
    if not state.merge_extracted_business_parameters(extracted, model_selected_order=state.understanding is not None):
        state.set_workflow_reply("本轮业务参数无效，请明确订单号和希望办理的事项。")
        state.needs_clarification = True
        return AgentNode.RESPONSE_GENERATION

    request_type = state.understanding.request_type if state.understanding else ("apply" if continuing_reason else None)
    decision = dependencies.policy.decide(
        topic=state.detected_intent,
        message=state.user_message,
        parameters=state.current_business_parameters().model_dump(exclude_none=True),
        smalltalk=bool(state.flow_value(FlowFlag.SMALLTALK)),
        request_type=request_type,
    )
    if state.understanding is None and state.detected_intent in TOPIC_POLICIES:
        from app.agent.request_understanding import ActionIntent
        kind = request_type or dependencies.policy.request_kind(state.detected_intent, state.user_message)
        if decision.needs_clarification:
            kind = "clarify"
        elif kind is None:
            kind = {"query_order": "query", "refund": "apply", "complaint": "apply", "human": "apply"}.get(
                state.detected_intent, "inquiry")
        state.action_intent = ActionIntent(topic=state.detected_intent, request_type=kind)
    state.route_reason = decision.reason
    state.missing_slots = list(decision.missing_slots)
    if decision.reply is not None:
        state.set_workflow_reply(decision.reply)
        state.needs_clarification = decision.needs_clarification
    return decision.next_node


def is_refund_inquiry(message: str, collected: Dict[str, Any]) -> bool:
    """兼容入口；订单参数不再决定一句话是否为政策咨询。"""
    return DEFAULT_ROUTING_POLICY.request_kind(IntentType.REFUND, message) == "inquiry"


def is_complaint_inquiry(message: str) -> bool:
    return DEFAULT_ROUTING_POLICY.request_kind(IntentType.COMPLAINT, message) == "inquiry"


def required_slots(state: AgentState) -> List[str]:
    return list(DEFAULT_ROUTING_POLICY.missing_slots(
        state.detected_intent, state.current_business_parameters().model_dump(exclude_none=True),
    ))


async def fill_slots(state: AgentState) -> str:
    """提示用户补齐业务工具所需的最小参数。"""
    prompts = {"order_id": "订单号", "reason": "退款原因"}
    labels = [prompts.get(slot, slot) for slot in state.missing_slots]
    if state.detected_intent == IntentType.REFUND:
        state.set_workflow_reply(f"为了处理退款申请，请补充：{'、'.join(labels)}。")
    elif state.detected_intent == IntentType.QUERY_ORDER:
        state.set_workflow_reply("为了查询您的订单，请提供订单号。")
    else:
        state.set_workflow_reply(f"为了查询订单，请提供：{'、'.join(labels)}。")
    return AgentNode.RESPONSE_GENERATION


async def clarify_request(dependencies: "ClarificationDependencies", state: AgentState) -> str:
    from app.agent.answer_contracts import CLARIFICATION_QUESTIONS
    question = dependencies.recognizer.get_clarification_question(state.detected_intent)
    if question == CLARIFICATION_QUESTIONS.get(state.detected_intent):
        state.set_workflow_reply(question)
    else:
        state.reply = question
    state.needs_clarification = True

    return AgentNode.RESPONSE_GENERATION


def intent_destination(intent: str) -> str:
    return DEFAULT_ROUTING_POLICY.destination(intent)


# 旧内部私有方法兼容；依赖按需访问，保留仅传部分服务的旧测试/调用。
async def intent_recognition_node(graph, state: AgentState) -> str:
    from app.agent.nodes.tool_node import persistent_actions
    return await recognize_request(IntentDependencies(
        getattr(graph, "intent_recognizer", None), lambda: graph.llm_service,
        lambda value: getattr(graph, "_refund_confirmed", lambda _: False)(value),
        lambda value: getattr(graph, "_refund_cancelled", lambda _: False)(value),
        lambda text: graph._is_smalltalk(text),
        lambda history, current: getattr(
            graph, "_detect_intent_escalation", detect_escalation
        )(history, current),
        persistent_actions(),
    ), state)


async def route_decision_node(graph, state: AgentState) -> str:
    return await route_request(RoutingDependencies(graph._extract_info_from_message), state)


def detect_intent_escalation(graph, intent_history: List[str], current_intent: str) -> Optional[List[str]]:
    return detect_escalation(intent_history, current_intent)


def is_refund_policy_inquiry(graph, message: str, collected: Dict[str, Any]) -> bool:
    return is_refund_inquiry(message, collected)


def is_complaint_policy_inquiry(graph, message: str) -> bool:
    return is_complaint_inquiry(message)


def missing_slots_for_intent(graph, state: AgentState) -> List[str]:
    return required_slots(state)


async def slot_filling_node(graph, state: AgentState) -> str:
    return await fill_slots(state)


async def clarification_node(graph, state: AgentState) -> str:
    return await clarify_request(ClarificationDependencies(graph.intent_recognizer), state)
