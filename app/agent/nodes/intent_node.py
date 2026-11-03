"""意图理解链路节点：意图识别、路由决策、槽位补齐、澄清。

自 graph.py 拆分迁移（原 AgentGraph._node_intent_recognition 等），逻辑保持原样：
路由决策直接修改 state 后返回 next_node，图由 graph.py 统一装配。
"""
from app.agent.flow_contracts import FlowFlag


import logging
from typing import Any, Dict, List, Optional, Callable, Protocol
from dataclasses import dataclass
from app.agent.intent import IntentResult

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
    refund_inquiry: Callable[[str, dict], bool]
    complaint_inquiry: Callable[[str], bool]
    missing_slots: Callable[[AgentState], list[str]]
    destination: Callable[[str], str]


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
        state.reply = SHADOW_NOTICE
        return AgentNode.RESPONSE_GENERATION
    if state.collected_info.get("refund_preview") and dependencies.cancelled(state):
        action_id = state.collected_info["refund_preview"].get("action_id")
        if action_id:
            await dependencies.actions.cancel(
                state.actor_id or state.user_id, state.user_id, action_id
            )
        state.detected_intent = IntentType.REFUND
        state.intent_confidence = 1.0
        state.needs_clarification = False
        state.pending_confirmations = []
        state.collected_info.pop("refund_preview", None)
        state.collected_info.pop("confirm_refund", None)
        state.collected_info.pop("refund_action", None)
        state.reply = "已取消本次退款申请，未执行任何退款操作。"
        return AgentNode.RESPONSE_GENERATION

    # 确认回复通常只有两个字，优先沿用待确认动作的意图，避免被识别为 general。
    if state.collected_info.get("refund_preview") and dependencies.confirmed(state):
        state.detected_intent = IntentType.REFUND
        state.intent_confidence = 1.0
        state.needs_clarification = False
        return AgentNode.ROUTE_DECISION

    mode = state.understanding_mode or settings.REQUEST_UNDERSTANDING_MODE
    if mode in {"model", "shadow"} and not dependencies.smalltalk(state.user_message):
        from app.agent.request_understanding import understand, route_understanding

        result = await understand(dependencies.model_factory(), state)
        if mode == "model":
            if result:
                return route_understanding(state, result)
            state.reply = "本轮请求理解暂时不可用，请明确说明咨询对象或希望办理的操作。"
            state.needs_clarification = True
            return AgentNode.RESPONSE_GENERATION
        state.collected_info["shadow_understanding"] = (
            result.model_dump() if result else {"status": "unavailable"}
        )
        # 影子运行只给只读结果；不让旧规则动作写入污染比较。
        state.reply = "影子理解已记录，本轮不执行申请或变更。"
        return AgentNode.RESPONSE_GENERATION

    intent_result = dependencies.recognizer.recognize(
        message=state.user_message,
        context=state.messages,
        history_intent=state.detected_intent
        if state.detected_intent != IntentType.GENERAL
        else None,
        llm_primary=settings.LLM_INTENT_PRIMARY,
    )

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
    if (
        intent_result.needs_llm_fallback
        and not is_smalltalk
        and not is_continuation_reply
    ):
        refined = await dependencies.model_factory().classify_intent(
            state.user_message, context=state.messages[-6:]
        )
        if refined:
            if settings.LLM_INTENT_PRIMARY:
                # LLM 主判：模型返回即采纳（模型不可用/失败返回 None 时保留规则）。
                intent_result.intent = refined.get("intent", intent_result.intent)
                intent_result.confidence = float(
                    refined.get("confidence", intent_result.confidence)
                )
                intent_result.needs_clarification = bool(
                    refined.get("need_clarification", False)
                )
                state.collected_info["intent_source"] = "llm_primary"
            elif refined.get("confidence", 0) > intent_result.confidence:
                # 旧兜底口径：仅当置信度更高时才覆盖规则结果。
                intent_result.intent = refined.get("intent", intent_result.intent)
                intent_result.confidence = float(
                    refined.get("confidence", intent_result.confidence)
                )
                intent_result.needs_clarification = bool(
                    refined.get("need_clarification", False)
                )
                state.collected_info["intent_source"] = "llm_fallback"

    state.detected_intent = intent_result.intent
    state.intent_confidence = intent_result.confidence
    state.needs_clarification = intent_result.needs_clarification

    if intent_result.intent == IntentType.HUMAN:
        state.need_human = True
        if not state.human_reason:
            state.human_reason = "用户请求转接人工客服"

    # 意图序列分析：检测升级模式（如 query_order → refund → complaint）
    intent_history = state.collected_info.get("intent_history", [])
    if intent_history:
        escalation = dependencies.escalation(
            intent_history, intent_result.intent
        )
        if escalation and not state.need_human:
            state.need_human = True
            state.human_reason = f"意图升级模式检测: {' -> '.join(escalation)}"

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
    extracted = dependencies.extract_info(state.user_message)
    if not state.merge_extracted_business_parameters(extracted, model_selected_order=state.understanding is not None):
        state.reply, state.needs_clarification = "本轮业务参数无效，请明确订单号和希望办理的事项。", True
        return AgentNode.RESPONSE_GENERATION

    # Answer reuse happens only after rewriting/retrieval, with current evidence.

    # 退款意图分流：政策咨询类（无订单号且询问流程/时效/规则）走 RAG，
    # 实际申请退款（有订单号+动作词）才走工具执行。
    if state.detected_intent == IntentType.REFUND and dependencies.refund_inquiry(
        state.user_message, state.current_business_parameters().model_dump(exclude_none=True)
    ):
        state.route_reason = "refund:policy_inquiry->rag"
        return AgentNode.QUERY_REWRITE

    # 投诉意图分流：政策咨询类（问流程/时效/材料/影响）走 RAG 回答，
    # 明确提交投诉/举报（动作词）才走工具创建工单。
    if (
        state.detected_intent == IntentType.COMPLAINT
        and dependencies.complaint_inquiry(state.user_message)
    ):
        state.route_reason = "complaint:policy_inquiry->rag"
        return AgentNode.QUERY_REWRITE

    missing = dependencies.missing_slots(state)
    state.missing_slots = missing
    if missing:
        state.route_reason = f"missing_slots:{','.join(missing)}"
        return AgentNode.SLOT_FILLING

    # general 兜底意图三级链路：寒暄命中的走模板直达响应生成（不检索），
    # 其余泛问复用 technical/promotion 的改写→RAG 检索路径，由检索节点
    # 在无证据时兜底输出"建议转人工"话术。
    if state.detected_intent == IntentType.GENERAL:
        if state.flow_value(FlowFlag.SMALLTALK):
            state.route_reason = "general:smalltalk->template"
            return AgentNode.RESPONSE_GENERATION
        state.route_reason = "general:rag_fallback"
        return AgentNode.QUERY_REWRITE

    next_node = dependencies.destination(state.detected_intent)
    state.route_reason = f"intent:{state.detected_intent}->{next_node}"
    return next_node


def is_refund_inquiry(
    message: str, collected: Dict[str, Any]
) -> bool:
    """判断退款意图是否为政策咨询（应走 RAG）而非实际退款申请（应走工具）。"""
    policy_keywords = [
        "流程",
        "几天",
        "多久",
        "怎么退",
        "如何退",
        "政策",
        "规则",
        "到账",
        "无理由",
        "质保期",
        "保修期",
        "售后",
        "换货流程",
        "退货流程",
        "运费谁出",
        "能退吗",
        "包装",
        "保留",
        "区别",
        "换货",
        "条件",
        "要求",
        "什么样",
        "需要注意",
        "规定",
        "原路",
        "返回",
        "从哪里退",
        "退到",
        "退款方式",
        "几天能到",
        "什么时候到",
    ]
    action_keywords = [
        "申请",
        "退钱",
        "帮我退",
        "给我退",
        "我要退",
        "直接退",
        "马上退",
        "退款成功",
    ]
    has_policy_kw = any(kw in message for kw in policy_keywords)
    has_action_kw = any(kw in message for kw in action_keywords)
    has_order_id = bool(collected.get("order_id"))
    # 有政策词且没有明确动作词、且没有订单号 → 政策咨询
    if has_policy_kw and not has_action_kw and not has_order_id:
        return True
    # "能退吗/可以退吗" 这类询问是否可退，无订单号 → 政策咨询
    if (
        any(kw in message for kw in ["能退吗", "可以退", "好退吗", "给退吗"])
        and not has_order_id
    ):
        return True
    return False


def is_complaint_inquiry(message: str) -> bool:
    """判断投诉意图是否为政策咨询（问流程/时效/材料/影响，应走 RAG），
    而非明确提交投诉/举报（应走工具创建工单）。"""
    policy_keywords = [
        "多久",
        "几天",
        "流程",
        "怎么投诉",
        "如何投诉",
        "怎么提交",
        "在哪里投诉",
        "哪里投诉",
        "材料",
        "匿名",
        "影响",
        "反馈",
        "进度",
        "电话",
        "入口",
        "工单",
    ]
    action_keywords = [
        "我要投诉",
        "我要举报",
        "投诉你们",
        "举报你们",
        "投诉你",
        "举报你",
        "帮我投诉",
        "帮我举报",
        "提交投诉",
        "投诉一下",
        "举报一下",
        "投诉一个",
        "举报一个",
    ]
    has_policy = any(kw in message for kw in policy_keywords)
    has_action = any(kw in message for kw in action_keywords)
    return has_policy and not has_action


def required_slots(state: AgentState) -> List[str]:
    collected = state.current_business_parameters().model_dump(exclude_none=True)
    if state.detected_intent == IntentType.QUERY_ORDER:
        return ["order_id"] if not collected.get("order_id") else []
    if state.detected_intent == IntentType.REFUND:
        missing = []
        if not collected.get("order_id"):
            missing.append("order_id")
        reason = str(collected.get("reason") or "").strip()
        if not reason or len(reason) < 6:
            missing.append("reason")
        return missing
    return []


async def fill_slots(state: AgentState) -> str:
    """提示用户补齐业务工具所需的最小参数。"""
    prompts = {"order_id": "订单号", "reason": "退款原因"}
    labels = [prompts.get(slot, slot) for slot in state.missing_slots]
    if state.detected_intent == IntentType.REFUND:
        state.reply = f"为了处理退款申请，请补充：{'、'.join(labels)}。"
    elif state.detected_intent == IntentType.QUERY_ORDER:
        # 缺订单号时不只索要参数，同时给出查找路径，避免客服"只问不改"。
        state.reply = (
            "为了查询您的订单，请提供订单号（订单 ORD 开头的编号）。"
            "您可以在 App/官网“我的订单”页面或下单短信中查看。"
        )
    else:
        state.reply = f"为了查询订单，请提供：{'、'.join(labels)}。"
    return AgentNode.RESPONSE_GENERATION


async def clarify_request(dependencies: "ClarificationDependencies", state: AgentState) -> str:
    question = dependencies.recognizer.get_clarification_question(state.detected_intent)
    state.reply = question
    state.needs_clarification = True

    state.messages.append(
        {
            "role": "assistant",
            "content": question,
            "type": "clarification",
        }
    )

    return AgentNode.RESPONSE_GENERATION


def intent_destination(intent: str) -> str:
    return {
        IntentType.QUERY_ORDER: AgentNode.TOOL_EXECUTION,
        IntentType.REFUND: AgentNode.TOOL_EXECUTION,
        IntentType.COMPLAINT: AgentNode.TOOL_EXECUTION,
        IntentType.TECHNICAL: AgentNode.QUERY_REWRITE,
        IntentType.PROMOTION: AgentNode.QUERY_REWRITE,
        IntentType.HUMAN: AgentNode.HUMAN_HANDOFF,
        IntentType.GENERAL: AgentNode.RESPONSE_GENERATION,
    }.get(intent, AgentNode.RESPONSE_GENERATION)


# 旧内部私有方法兼容；依赖按需访问，保留仅传部分服务的旧测试/调用。
async def intent_recognition_node(graph, state: AgentState) -> str:
    from app.agent.nodes.tool_node import persistent_actions
    return await recognize_request(IntentDependencies(
        getattr(graph, "intent_recognizer", None), lambda: graph.llm_service,
        lambda value: graph._refund_confirmed(value), lambda value: graph._refund_cancelled(value),
        lambda text: graph._is_smalltalk(text), lambda history, current: graph._detect_intent_escalation(history, current),
        persistent_actions(),
    ), state)


async def route_decision_node(graph, state: AgentState) -> str:
    return await route_request(RoutingDependencies(
        graph._extract_info_from_message, graph._is_refund_policy_inquiry,
        graph._is_complaint_policy_inquiry, graph._missing_slots_for_intent, graph._route_by_intent,
    ), state)


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
