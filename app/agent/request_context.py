"""请求理解的有界输入快照；历史仅解释指代，不作为事实或授权。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal
import re
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from app.agent.routing_policy import is_follow_up
from app.agent.context_relevance import product_mentions, related_topic, topics

if TYPE_CHECKING:
    from app.agent.state import AgentState

ORDER_PATTERN = re.compile(r"ORD[-_a-zA-Z0-9]*\d{4,}", re.I)
NEW_ORDER_PATTERN = re.compile(r"(?:另一个|另一笔|其他|别的)订单|另一单|换一(?:笔|个)订单|换一单")
_REFERENCE = re.compile(r"它|这个|那个|该产品|该订单|当前订单|这笔|这单|还是|仍然|继续|刚才")
_SHORT_ELISION = re.compile(r"^(?:那|大概|一般|预计)?(?:多久|多少钱|什么材料|需要什么材料)[？?。 ]*$")
_TOPIC_SWITCH = re.compile(r"换个话题|换一个话题|另一个问题|另外(?:问|咨询)|不聊|先不(?:说|谈|聊|处理)")
_QUESTION = re.compile(r"[？?]|谁|多久|如何|怎么|能否|是否|怎么办|多少钱|什么|哪些|几天|吗")
_ELIDED_QUESTION = re.compile(
    r"^(?:那|请问|一般|大概|预计)?(?:运费|费用|退款|退货|退回|换货|多久|多少钱|谁|需要|如何|怎么|能否|是否|材料|流程|时间|期限|到账|寄回|配送|物流|发货|收货|能不能|可以|要)")
HISTORY_MESSAGES = 7
MESSAGE_CHARS = 600
HISTORY_CHARS = 2400


def order_mentions(message: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.group() for match in ORDER_PATTERN.finditer(message)))


def needs_history(message: str) -> bool:
    return not NEW_ORDER_PATTERN.search(message) and bool(
        is_follow_up(message) or _REFERENCE.search(message) or _SHORT_ELISION.fullmatch(message))


def is_slot_reply(message: str) -> bool:
    if NEW_ORDER_PATTERN.search(message):
        return False
    if len(order_mentions(message)) == 1 and ORDER_PATTERN.fullmatch(message.strip()):
        return True
    return len(message) <= 40 and not re.search(
        r"怎么|如何|吗|退款|退货|投诉|人工|真人|物流|订单|会员|优惠|活动|安装|连接|维修|换个话题", message)


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    role: Literal["user", "assistant"]
    content: str
    source: Literal["history"] = "history"


class RequestContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    current_message: str
    history: tuple[HistoryMessage, ...] = ()
    known_order: str | None = None
    pending_action: bool = False
    unfinished_topic: str | None = None
    missing_slots: tuple[str, ...] = ()
    scope_digest: str = ""

    def matches(self, state: AgentState) -> bool:
        return self.scope_digest == request_scope_digest(state)

    def payload(self) -> dict:
        return {
            "current_message": self.current_message, "source": "current_message",
            "history": [item.model_dump() for item in self.history],
            "known_order": self.known_order, "pending_action": self.pending_action,
            "unfinished_topic": self.unfinished_topic,
            "missing_slots": list(self.missing_slots),
        }

    def classifier_history(self) -> list[dict]:
        return [{"role": item.role, "content": item.content} for item in self.history]


def bounded_history(messages: list[dict], current_message: str, *, remove_current: bool = True) -> tuple[HistoryMessage, ...]:
    # 只移除尾部的本轮消息一次，不能删除内容相同的真实历史。
    items = list(messages)
    if remove_current and items and items[-1].get("role") == "user" and items[-1].get("content") == current_message:
        items.pop()
    selected = []
    remaining = HISTORY_CHARS
    # 先限制实际最近窗口，不能在大量无关历史中重新捞回旧对象。
    for item in reversed(items[-HISTORY_MESSAGES:]):
        if item.get("role") not in {"user", "assistant"} or not isinstance(item.get("content"), str):
            continue
        content = item["content"][:min(MESSAGE_CHARS, remaining)]
        if content:
            selected.append(HistoryMessage(role=item["role"], content=content))
            remaining -= len(content)
        if remaining == 0:
            break
    return tuple(reversed(selected))


def build_request_context(state: AgentState) -> RequestContext:
    message = state.user_message
    recent = bounded_history(state.messages, message)
    mentioned = order_mentions(message)
    known_order = state.current_business_parameters().order_id
    object_changed = bool(mentioned and known_order and any(order != known_order for order in mentioned))
    reset_topic = bool(_TOPIC_SWITCH.search(message) or NEW_ORDER_PATTERN.search(message) or object_changed)
    follow_up = needs_history(message) and not reset_topic
    # 补参中的简短答案可参考背景；明确换对象时禁止借用旧订单。
    task_pending = bool(state.task and state.task.status in {"waiting_input", "waiting_confirmation"}
                        and state.task.user_id == state.user_id and state.task.session_id == state.session_id
                        and state.task.actor_id == (state.actor_id or state.user_id))
    unfinished = bool(state.needs_clarification or state.missing_slots or task_pending)
    previous_user = next((item.content for item in reversed(recent) if item.role == "user"), "")
    anchor = state.task.goal if task_pending else previous_user
    previous_products = product_mentions(anchor)
    current_products = product_mentions(message)
    reset_topic = reset_topic or bool(previous_products and current_products
                                     and previous_products.isdisjoint(current_products))
    question = bool(_QUESTION.search(message))
    same_topic = bool(anchor and related_topic(message, anchor))
    # 短追问可跨具体词衔接同一业务主题，例如包装损坏后询问运费。
    short_question = bool(len(message) <= 24 and question and _ELIDED_QUESTION.search(message)
                          and topics(message) & topics(anchor))
    # 排障反馈常无指代词，仅在任务确实等待反馈且未明显换业务时补足背景。
    feedback = bool(task_pending and state.task.waiting_for == "diagnostic_feedback"
                    and len(message) <= 40 and not question
                    and not (topics(message) - {"technical"}))
    slot_pending = bool(state.needs_clarification or state.missing_slots or (task_pending and
                        state.task.waiting_for in {"order_id", "reason", "product", "request"}))
    use_history = not reset_topic and (follow_up or (slot_pending and is_slot_reply(message))
                                       or (question and same_topic) or short_question or feedback)
    history = recent if use_history else ()
    if task_pending and use_history and not any(state.task.goal in item.content for item in history):
        # 恢复提示仍是用户陈述；不导入旧工具结果或旧知识证据。
        history = (HistoryMessage(role="user", content=state.task.goal[:MESSAGE_CHARS]), *history[-3:])
    return RequestContext(
        current_message=state.user_message,
        history=history,
        known_order=known_order if use_history else None,
        pending_action=bool(state.refund_preview_data()) and use_history,
        unfinished_topic=state.detected_intent if unfinished and use_history else None,
        missing_slots=tuple(state.missing_slots) if unfinished and use_history else (),
        scope_digest=request_scope_digest(state),
    )


def request_scope_digest(state: AgentState) -> str:
    return hashlib.sha256(json.dumps(
        [state.user_id, state.actor_id, state.session_id, state.source_message_id, state.user_message],
        ensure_ascii=False).encode()).hexdigest()


def ambiguous_order_question(message: str) -> str | None:
    mentions = order_mentions(message)
    if len(mentions) > 1:
        return "本轮提到了多个订单，请明确要查询或办理的是哪一个订单。"
    if NEW_ORDER_PATTERN.search(message) and not mentions:
        return "请提供另一个订单的订单号；我不会沿用之前的订单办理。"
    return None


def stage_context(state: AgentState) -> dict:
    """所有模型阶段共用有界来源选择；摘要以数据呈现，不能提升为system指令。"""
    context = build_request_context(state)
    payload = context.payload()
    if context.history:
        from app.services.conversation_summary import summary_context
        payload["historical_summary"] = summary_context(state.conversation_summary)
    if state.task_runtime or (state.task and context.history):
        from app.agent.task_context import task_context_payload
        payload["task_memory"] = task_context_payload(state)
    from app.agent.context_budget import fit_context
    return fit_context(payload)


class RefinedIntent(BaseModel):
    """旧分类器的严格出口；confidence只作为观察，不用于跨量纲覆盖。"""
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    intent: Literal["query_order", "refund", "complaint", "technical", "promotion", "human", "general"]
    confidence: float = Field(ge=0, le=1)
    need_clarification: bool
