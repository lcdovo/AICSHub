"""响应生成节点：LLM 生成、回答缓存、工具模板、RAG 引用与 LLM judge 校验。

ResponseNode在组件内部组织生成、审查、引用和缓存助手，主图仅注入服务。
保留旧Graph私有方法的结构兼容，常量与纯模板属于本响应域；校验标准不因注入改变。
"""
from app.agent.flow_contracts import FlowFlag

import json
import hashlib
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Callable, Protocol, Awaitable
from dataclasses import dataclass
from langchain_core.runnables import RunnableLambda
from datetime import datetime

from app.agent.state import AgentState, AgentNode, IntentType
from app.config.config import settings
from app.services.user_memory import get_user_memory_service
from app.agent.handoff_evidence import matching_handoff_receipt

logger = logging.getLogger(__name__)

# 高频寒暄语分组词表：问候 / 感谢 / 告别。
# general 意图命中的消息直接走固定模板回复，不调 LLM、不进 RAG 检索。
# 注意：不收录"好的/嗯"等短应答，保持现有澄清行为。
SMALLTALK_PATTERNS = {
    "greeting": ["你好", "您好", "hi", "hello", "在吗", "在不在", "哈喽", "嗨", "早上好", "中午好", "下午好", "晚上好", "hey"],
    "thanks": ["谢谢", "感谢", "多谢", "thanks", "thank"],
    "farewell": ["再见", "拜拜", "bye", "回见"],
}

# ---------- 延迟优化：回答缓存与工具模板 ----------
ANSWER_CACHE_TTL = 120.0
ANSWER_CACHE_MAX = 500

# ---------- 动作确认型工具模板 ----------
# 解释性问法：用户要的是解释而非状态回显，任何动作确认型工具都不走模板。
EXPLAIN_MARKERS = ("为什么", "怎么回事", "咋回事", "怎么办", "怎么处理", "能退吗", "多久")
# 强情绪词：转人工场景命中时安抚结语需 LLM 组织，模板化会显得生硬。
STRONG_EMOTION_MARKERS = ("气死", "垃圾", "太气人", "打不通", "敷衍", "坑惨", "真服了", "不回消息", "拖延")


class AnswerModel(Protocol):
    async def chat(self, **payload) -> tuple[str, str, int]: ...
    def stream_chat(self, **payload) -> Any: ...
    def get_last_call_metadata(self) -> dict: ...


class AnswerCache(Protocol):
    async def aget(self, key: str) -> Any: ...
    async def aset(self, key: str, value: Any, *, ttl: float) -> None: ...


class AnswerValidator(Protocol):
    def validate(self, **payload) -> Any: ...


class AnswerJudge(Protocol):
    DEFAULT_THRESHOLD: float
    async def judge(self, **payload) -> dict | None: ...


class KnowledgeSnapshot(Protocol):
    def evidence_is_current(self, items: list[dict]) -> bool: ...


class UserMemoryProfile(Protocol):
    async def get_user_profile(self, **payload) -> dict: ...


@dataclass(frozen=True)
class ResponseServices:
    model_factory: Callable[[], AnswerModel]
    validator_factory: Callable[[], AnswerValidator]
    judge_factory: Callable[[], AnswerJudge]
    cache_factory: Callable[[], AnswerCache]
    knowledge_factory: Callable[[], KnowledgeSnapshot]
    memory_factory: Callable[[], UserMemoryProfile]


class ResponseContext(Protocol):
    """组件内局部助手契约；旧Graph兼容适配器也满足该结构。"""
    @property
    def llm_service(self) -> AnswerModel: ...
    @property
    def validator(self) -> AnswerValidator: ...
    @property
    def llm_judge(self) -> AnswerJudge: ...
    llm_runnable: Any
    @property
    def _answer_cache(self) -> AnswerCache: ...
    _ANSWER_CACHE_TTL: float
    _EXPLAIN_MARKERS: tuple[str, ...]
    _STRONG_EMOTION_MARKERS: tuple[str, ...]
    _clean_chunk: Callable[[str, str], tuple[str, str, str]]
    _build_llm_prompt: Callable[[AgentState], str]
    _build_context_messages: Callable[[AgentState], list]
    _add_rag_citations: Callable[[str, list], str]
    _answer_cache_get: Callable[[str], Awaitable[str | None]]
    _answer_cache_set: Callable[[str, str], Awaitable[None]]
    _build_answer_cache_key: Callable[[AgentState], str]
    _generate_response_with_llm: Callable[[AgentState], Awaitable[str | None]]
    _get_default_reply: Callable[[str, str], str]
    _try_templated_tool_reply: Callable[[AgentState], str | None]
    _build_order_reply_template: Callable[[dict], str]
    _try_action_reply_template: Callable[[AgentState, str, dict | None], str | None]
    _build_refund_reply_template: Callable[[dict], str]
    _build_ticket_status_reply_template: Callable[[dict], str]
    _build_escalate_reply_template: Callable[[dict], str]
    _rag_judge_applicable: Callable[[AgentState], bool]
    _tool_evidence_text: Callable[[AgentState], str]


async def current_knowledge_evidence(context: ResponseContext, items: list[dict]) -> bool:
    import asyncio
    factory = getattr(context, "knowledge_factory", None)
    if factory is None:  # 仅供旧Graph私有方法兼容。
        from app.services.knowledge_base import get_knowledge_base
        factory = get_knowledge_base
    return await asyncio.to_thread(lambda: factory().evidence_is_current(items))


async def validate_candidate(self: "ResponseContext", state: AgentState) -> bool:
    """The same gate checks every generated draft, including regeneration."""
    if state.current_evidence() and any(item.get("version_id") for item in state.current_evidence()):
        if not await current_knowledge_evidence(self, state.current_evidence()):
            state.invalidate_evidence("knowledge_version_changed")
            state.reply="知识版本已变化，请重新提问以读取最新发布内容。"
    state.collected_info.pop("llm_judge", None)
    state.collected_info.pop("inline_self_check", None)
    # Citations are supporting material, not part of the answer being assessed.
    body = re.split(r"\n+\s*参考来源[：:]", state.reply, maxsplit=1)[0].rstrip()
    state.reply = body
    validation = self.validator.validate(
        response=body, user_query=state.user_message, intent=state.detected_intent,
        tool_results=[tc.result for tc in state.tool_calls if isinstance(tc.result, dict)],
    )
    report = validation.to_dict()
    passed = validation.passed
    if self._rag_judge_applicable(state) and not state.flow_value(FlowFlag.CACHE_HIT):
        from app.agent.validation_llm import assess_judgement, unknown_judgement
        try:
            judgement = await self.llm_judge.judge(
                response=body, user_query=state.user_message,
                kb_context=state.current_evidence(), tool_evidence=self._tool_evidence_text(state),
            )
        except Exception:
            judgement = unknown_judgement("judge_dependency_failed")
        judgement = assess_judgement(judgement)
        state.collected_info["llm_judge"] = judgement
        refused = judgement["support"] != "supported"
        passed = passed and not refused
        report["judge_passed"] = not refused
        report["evidence_score"] = judgement["fact_score"]
        report["evidence_support"] = judgement["support"]
        if state.evidence_bundle is not None:
            state.evidence_bundle.support = judgement["support"]
        if refused:
            report["overall_score"] = min(report["overall_score"], judgement["fact_score"] or 0)
            report["issues"].append({"type": "evidence_" + judgement["support"], "severity": "error"})
        state.set_flow(rag_refused=refused)
    from app.agent.request_understanding import ReviewResult
    state.review_result=ReviewResult(passed=passed, evidence_version=(state.evidence_bundle.fingerprint() if state.evidence_bundle else None))
    report["passed"] = passed
    state.collected_info["validation"] = report
    state.set_flow(answer_validated=passed)
    return passed


async def response_generation_node(self: "ResponseContext", state: AgentState) -> str:
    is_shadow = (state.understanding_mode or settings.REQUEST_UNDERSTANDING_MODE) == "shadow"
    if is_shadow:
        # 影子只发布可信流程通知，不生成业务草稿或因审查失败转入写入型人工流程。
        from app.agent.request_understanding import SHADOW_NOTICE
        state.reply = SHADOW_NOTICE
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
            state.reply = handoff_message(receipt)
        elif state.flow_value(FlowFlag.HANDOFF_ATTEMPTED) or any(call.tool_name == "escalate_to_human" for call in state.tool_calls):
            state.reply = "暂时无法确认转人工请求结果，请查看请求记录或联系支持。"
        else:
            state.reply = self._try_templated_tool_reply(state) or "建议由人工进一步协助；当前尚未创建转人工请求，请回复“转人工”发起。"
    if state.flow_value(FlowFlag.SMALLTALK) and not state.reply:
        state.reply = self._get_default_reply(IntentType.GENERAL, state.user_message)

    if state.current_evidence() and any(item.get("version_id") for item in state.current_evidence()):
        if not await current_knowledge_evidence(self, state.current_evidence()):
            state.invalidate_evidence("knowledge_version_changed")
            state.reply="知识版本已变化，请重新提问以读取最新发布内容。"
    has_business_tool = any(tc.tool_name != "hybrid_search" for tc in state.tool_calls)
    grounded = bool(not is_shadow and state.flow_value(FlowFlag.GROUNDED) and state.current_evidence())
    answer_cache_key = None
    generated_by_llm = False
    if not state.reply:
        templated = self._try_templated_tool_reply(state)
        if templated:
            state.reply = templated
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
        if grounded and not has_business_tool:
            answer_cache_key = self._build_answer_cache_key(state)
            cached = await self._answer_cache_get(answer_cache_key)
            if cached is not None:
                state.reply = cached
                state.set_flow(answer_from_cache=True)
        if not state.reply:
            state.reply = await self._generate_response_with_llm(state)
            generated_by_llm = bool(state.reply)
            if not state.reply:
                state.reply = self._get_default_reply(state.detected_intent, state.user_message)

    # Workflow prompts and completed handoffs are terminal messages, not drafts.
    handoff_done = bool(state.flow_value(FlowFlag.HANDOFF_ATTEMPTED)) or any(
        tc.tool_name == "escalate_to_human" for tc in state.tool_calls
    )
    workflow_reply = bool(
        is_shadow or state.missing_slots or state.needs_clarification
        or state.flow_value(FlowFlag.SMALLTALK)
        or state.flow_value(FlowFlag.GROUNDED) is False or handoff_done
    )
    if not workflow_reply:
        passed = await validate_candidate(self, state)
        if not passed and generated_by_llm and not state.flow_value(FlowFlag.REFUSED):
            # A replacement must pass the entire gate again, including the judge.
            state.collected_info.pop("inline_self_check", None)
            retry_reply = await self._generate_response_with_llm(state)
            if retry_reply:
                state.reply = retry_reply
                passed = await validate_candidate(self, state)
        if state.flow_value(FlowFlag.REFUSED):
            state.reply = (
                "抱歉，我暂时没有足够可靠的依据回答这个问题。"
                "为避免误导，建议您补充更具体的信息，或回复“转人工”由客服协助确认。"
            )
        elif not passed:
            if state.read_plan:
                # 只读计划审查失败仍须只读终止，不能隐式创建人工请求。
                state.reply = "本轮查询结果未通过校验，请核对查询对象后重试；没有执行后续业务操作。"
                state.needs_clarification = True
            else:
                state.need_human = True
                state.human_reason = "生成后回答校验未通过"
                return AgentNode.HUMAN_HANDOFF

    if grounded and state.flow_value(FlowFlag.GROUNDED) and not state.flow_value(FlowFlag.REFUSED) and not handoff_done:
        state.reply = self._add_rag_citations(state.reply, state.current_evidence())
    # 所有公开文本共用此底线，包括工作流、可信模板、缓存和最终引用。
    # 失败只替换本轮文本，不能跳到人工节点引发额外写操作。
    from app.agent.publication_guard import publication_issues, SAFE_PUBLICATION_REPLY
    issues = publication_issues(state.reply)
    from app.agent.critical_facts import critical_fact_issues
    issues.extend(item["type"] for item in critical_fact_issues(
        state.reply, [call.result for call in state.tool_calls if isinstance(call.result, dict)]
    ))
    state.collected_info["publication_guard"] = {"passed": not issues, "issues": issues}
    if issues:
        from app.agent.request_understanding import ReviewResult
        state.reply = SAFE_PUBLICATION_REPLY
        state.set_flow(answer_validated=False)
        state.review_result = ReviewResult(passed=False, evidence_version=(state.evidence_bundle.fingerprint() if state.evidence_bundle else None))
    if (generated_by_llm and answer_cache_key and state.flow_value(FlowFlag.VALIDATED)
            and state.flow_value(FlowFlag.GROUNDED) and state.current_evidence()
            and not state.need_human and not state.flow_value(FlowFlag.REFUSED)
            and not state.current_generation_metadata().degraded):
        await self._answer_cache_set(answer_cache_key, state.reply)

    state.messages.append({
        "role": "assistant", "content": state.reply, "intent": state.detected_intent,
        "tool_calls": [tc.tool_name for tc in state.tool_calls],
        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
    })
    # Buffer provider tokens until the final answer has passed the gate.
    from app.agent.stream_context import stream_event_queue as _stream_event_queue
    stream_queue = _stream_event_queue.get()
    if stream_queue is not None:
        for offset in range(0, len(state.reply), 64):
            await stream_queue.put(("token", state.reply[offset:offset + 64]))
    return AgentNode.END


def add_rag_citations(self: "ResponseContext", reply: str, results: list[dict[str, Any]]) -> str:
    """Expose the exact published chunks used by the answer."""
    # The server supplies references from current evidence, including on cache hits.
    reply = re.split(r"\n+\s*参考来源[：:]", reply, maxsplit=1)[0].rstrip()
    citations = []
    for item in results[:3]:
        title, chapter, body = self._clean_chunk(
            str(item.get("content", "")), str(item.get("title", ""))
        )
        label = f"{title}（{chapter}）" if chapter else (title or "知识库")
        snippet = body[:120]
        citations.append(f"- {label}：{snippet}")
    return reply + ("\n\n参考来源：\n" + "\n".join(citations) if citations else "")


def clean_chunk(content: str, fallback_title: str = "") -> tuple[str, str, str]:
    """清洗检索片段：剥离"文档：/章节："元信息前缀与 markdown 标记。

    返回 (文档标题, 章节, 清洗后的正文)。chunk 入库时由 _heading_prefix
    加了"文档：xxx\n章节：xxx\n"前缀，且分块保留了原文的 markdown 标题
    （## 四、常见使用问题），直接展示会暴露元信息与标记符号。
    """
    title, chapter, rest = fallback_title, "", (content or "")
    match = re.match(r"^文档：(.*?)\n章节：(.*?)\n", rest)
    if match:
        title = (match.group(1).strip() or fallback_title)
        chapter = match.group(2).strip()
        rest = rest[match.end():]
    # 去掉 markdown 标记：标题 #、粗体/斜体 *、行内码 `、引用 >、下划线 _
    rest = re.sub(r"[#*`>_]", "", rest)
    body = " ".join(rest.split())
    return title, chapter, body


def build_answer_cache_key(self: "ResponseContext", state: AgentState) -> str:
    """Scope reuse to the user, semantic context, current evidence and policy."""
    evidence = [
        {key: item.get(key) for key in ("id", "title", "content", "source", "version")}
        for item in state.current_evidence()[:3]
    ]
    payload = {
        "user_id": state.user_id, "intent": state.detected_intent,
        "query": state.rewritten_query or state.user_message,
        "prompt": self._build_llm_prompt(state),
        "history": [{"role": m.get("role"), "content": m.get("content")} for m in state.messages],
        "summary": state.conversation_summary, "evidence": evidence,
        "evidence_snapshot": state.evidence_bundle.fingerprint() if state.evidence_bundle else None,
        "versions": [settings.LLM_PROMPT_VERSION, settings.LLM_MODEL_VERSION,
                     settings.LLM_SCHEMA_VERSION, settings.LLM_ROUTE_VERSION],
        "model": [settings.LLM_API_BASE, settings.LLM_MODEL, settings.LLM_BACKUP_BASE,
                  settings.LLM_BACKUP_MODEL, settings.LLM_THINKING_MODE,
                  settings.LLM_STRUCTURED_OUTPUT_METHOD],
        "judge": settings.LLM_JUDGE_ENABLED,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    # M5收紧审查后不能复用旧版本曾在Judge未知时放行的缓存。
    return f"validated-v3:{digest}"


async def answer_cache_get(self: "ResponseContext", key: str) -> Optional[str]:
    value = await self._answer_cache.aget(key)
    if isinstance(value, dict) and value.get("schema") == 2 and value.get("validated") is True:
        reply = value.get("reply")
        return reply if isinstance(reply, str) and reply else None
    return None


async def answer_cache_set(self: "ResponseContext", key: str, reply: str):
    await self._answer_cache.aset(
        key, {"schema": 2, "validated": True, "reply": reply}, ttl=self._ANSWER_CACHE_TTL,
    )


def try_templated_tool_reply(self: "ResponseContext", state: AgentState) -> Optional[str]:
    """工具成功后用模板拼接回复，跳过 LLM 生成（延迟优化）。"""
    if not state.tool_calls:
        return None
    last_call = state.tool_calls[-1]
    if not last_call.success:
        return None
    data = (last_call.result or {}).get("data")
    intent = state.detected_intent

    if intent == IntentType.QUERY_ORDER and last_call.tool_name == "query_order" and isinstance(data, dict):
        # 仅对"查状态/物流"类问题用模板；取消/原因/改地址等需要解释性回答，
        # 交回 LLM 生成，避免模板只回显状态而丢失对动作诉求的处理。
        non_template_markers = ("取消", "为什么", "怎么回事", "咋回事", "拦截", "改地址", "退回", "退款", "退货", "怎么改", "能退")
        if any(k in state.user_message for k in non_template_markers):
            return None
        return self._build_order_reply_template(data)

    if intent == IntentType.COMPLAINT and last_call.tool_name == "create_ticket" and isinstance(data, dict):
        message = str(data.get("message") or "").strip()
        if message:
            return message

    # 动作确认型工具模板：apply_refund / update_ticket_status / escalate_to_human。
    # 结果为结构化 dict 且用户问法是"确认动作结果"时直接拼模板，跳过 LLM 生成；
    # 解释性问法或强情绪场景仍交回 LLM（见 try_action_reply_template）。
    return self._try_action_reply_template(state, last_call.tool_name, data)


def build_order_reply_template(data: Dict[str, Any]) -> str:
    """根据 query_order 工具结果拼出可直接回复的模板文案。"""
    order_id = str(data.get("order_id") or "")
    status = str(data.get("status") or "")
    shipping = data.get("shipping") or {}
    if isinstance(shipping, str):
        try:
            shipping = json.loads(shipping)
        except (TypeError, json.JSONDecodeError):
            shipping = {}
    carrier = str(shipping.get("carrier") or "")
    tracking = str(shipping.get("tracking_no") or shipping.get("tracking") or "")
    eta = str(shipping.get("eta") or shipping.get("expected_delivery") or "")
    amount = data.get("total_amount")
    try:
        amount_text = f"总金额 {Decimal(str(amount)):.2f} 元" if amount is not None else ""
    except InvalidOperation:
        amount_text = ""
    items = data.get("items") or []
    product = ""
    if isinstance(items, list) and items and isinstance(items[0], dict):
        product = str(items[0].get("name") or items[0].get("product_name") or "")

    parts = [f"您的订单 {order_id}", f"当前状态：{status}"]
    if carrier:
        parts.append(f"承运方：{carrier}")
    if tracking:
        parts.append(f"运单号：{tracking}")
    if eta:
        parts.append(f"预计送达：{eta}")
    if product:
        parts.append(f"商品：{product}")
    if amount_text:
        parts.append(amount_text)
    return "，".join(parts) + "。"


def try_action_reply_template(
    self: "ResponseContext", state: AgentState, tool_name: str, data: Optional[Dict[str, Any]]
) -> Optional[str]:
    """动作确认型工具（apply_refund / update_ticket_status / escalate_to_human）成功后拼模板回复。"""
    if not isinstance(data, dict) or not tool_name:
        return None
    if any(k in state.user_message for k in self._EXPLAIN_MARKERS):
        return None

    if tool_name == "apply_refund":
        return self._build_refund_reply_template(data)
    if tool_name == "update_ticket_status":
        return self._build_ticket_status_reply_template(data)
    if tool_name == "escalate_to_human":
        if any(k in state.user_message for k in self._STRONG_EMOTION_MARKERS):
            return None
        return self._build_escalate_reply_template(data)
    return None


def build_refund_reply_template(data: Dict[str, Any]) -> str:
    """根据 apply_refund 工具结果拼出可直接回复的模板文案。"""
    refund_id = str(data.get("refund_id") or "")
    order_id = str(data.get("order_id") or "")
    type_label = str(data.get("type_label") or "退款")
    status_label = str(data.get("status_label") or "待审核")
    amount = data.get("amount")
    try:
        amount_text = f"金额 {Decimal(str(amount)):.2f} 元" if amount is not None else ""
    except InvalidOperation:
        amount_text = ""

    parts = ["您的模拟退换货申请已登记" if data.get("is_simulated") else "您的退换货申请已提交"]
    if refund_id:
        parts.append(f"申请单号 {refund_id}")
    if order_id:
        parts.append(f"订单 {order_id}")
    parts.append(f"类型：{type_label}")
    if amount_text:
        parts.append(amount_text)
    parts.append(f"登记时状态：{status_label}" if data.get("is_simulated") else f"当前状态：{status_label}")
    # 取 next_steps 首条的后续指引（去掉"1. "编号前缀），避免模板冗长。
    steps = data.get("next_steps")
    if isinstance(steps, list) and steps:
        first = re.sub(r"^\s*\d+[\.、]?\s*", "", str(steps[0])).strip()
        if first:
            parts.append(first)
    return "，".join(parts) + "。"


def build_ticket_status_reply_template(data: Dict[str, Any]) -> str:
    """根据 update_ticket_status 工具结果拼出可直接回复的模板文案。"""
    ticket_id = str(data.get("ticket_id") or "")
    label = str(data.get("new_status_label") or data.get("new_status") or "已更新")
    return f"工单 {ticket_id} 状态已更新为：{label}。"


def build_escalate_reply_template(data: Dict[str, Any]) -> str:
    """只描述人工请求的持久状态，不补造队列或接通事实。"""
    from app.services.ticket_creation import handoff_message
    return handoff_message(data)


def rag_judge_applicable(self: "ResponseContext", state: AgentState) -> bool:
    """All grounded policy answers use the gate, regardless of their intent label."""
    return (
        bool(state.flow_value(FlowFlag.GROUNDED))
        and not state.missing_slots
        and bool(state.current_evidence())
    )


def tool_evidence_text(state: AgentState) -> str:
    """把成功工具返回的数据压缩成给 judge 的证据文本。"""
    parts = []
    for tc in state.tool_calls:
        if tc.success and isinstance(tc.result, dict) and tc.result.get("data"):
            parts.append(json.dumps(tc.result["data"], ensure_ascii=False))
    return "\n".join(parts)


async def generate_response_with_llm(self: "ResponseContext", state: AgentState) -> str:
    """使用 LLM 生成响应，失败时返回 None 由调用方降级"""
    # 流式通道独立于图的装配，供应商草稿仍在组件内缓冲。
    from app.agent.stream_context import stream_event_queue as _stream_event_queue

    try:
        prompt = self._build_llm_prompt(state)
        context_messages = self._build_context_messages(state)

        payload = {
            "user_id": state.user_id,
            "session_id": state.session_id,
            "message": prompt,
            "context": context_messages,
        }
        stream_queue = _stream_event_queue.get()
        if stream_queue is not None:
            # Collect provider output privately; response_generation publishes the final validated text.
            chunks = []
            model = self.llm_service
            async for chunk in model.stream_chat(**payload):
                chunks.append(chunk)
            reply = "".join(chunks)
            metadata = model.get_last_call_metadata()
            state.record_generation_metadata(metadata)
            token_count = state.current_generation_metadata().tokens
            if token_count is None:
                token_count = max(1, len(reply) // 4)  # 兼容展示估算，不能冒充SQL provider usage。
        else:
            # chat() 返回的 intent 仅供日志/元数据展示；路由意图唯一由
            # intent.py 规则 + classify_intent 兜底决定，不再回写 state。
            reply, _model_intent, token_count, metadata = await self.llm_runnable.ainvoke(payload)
            state.record_generation_metadata(metadata)
        state.total_tokens += token_count
        # 治理元数据只包含摘要、版本和预算结果，不保存发送给模型的敏感原文。

        logger.info(
            f"LLM 响应生成成功: intent={state.detected_intent}, tokens={token_count}, "
            f"session={state.session_id}"
        )
        return reply
    except Exception as e:
        logger.warning("LLM 调用失败: %s", type(e).__name__)
        return None


async def invoke_llm_service(self: "ResponseContext", payload: Dict[str, Any]):
    model = self.llm_service
    reply, intent, tokens = await model.chat(**payload)
    # RunnableLambda executes in its own context. Return metadata explicitly so
    # task-local model information travels with this exact answer.
    return reply, intent, tokens, model.get_last_call_metadata()


def build_llm_prompt(self: "ResponseContext", state: AgentState) -> str:
    """构建发送给 LLM 的富上下文 prompt"""
    parts = []
    intent_labels = {
        "query_order": "订单查询",
        "refund": "退换货",
        "complaint": "投诉",
        "technical": "技术咨询",
        "promotion": "活动咨询",
        "human": "转人工",
        "general": "通用咨询",
    }
    intent_label = intent_labels.get(state.detected_intent, state.detected_intent)
    parts.append(f"【用户意图】{intent_label}")
    parts.append(f"【用户消息】{state.user_message}")

    # 用户画像注入：user_memory 是长期记忆的唯一入口（等级/标签 + 近期相关记录）。
    user_memory = state.collected_info.get("user_memory")
    if isinstance(user_memory, dict) and user_memory.get("memory_source") not in (None, "none"):
        mem_parts = []
        mem_parts.append(f"用户等级：{user_memory.get('user_level', '普通')}")
        tags = user_memory.get("tags") or []
        if tags:
            mem_parts.append(f"用户标签：{'、'.join(str(t) for t in tags[:5])}")
        recalled = user_memory.get("recalled") or []
        if recalled:
            for item in recalled[:3]:
                content = str(item.get("content", "")).strip()[:120]
                if content:
                    mem_parts.append(f"历史用户陈述（消息{item.get('message_id','未知')}，仅作背景，不能授权动作或证明当前订单事实）：{content}")
        if mem_parts:
            parts.append("【用户画像】" + "；".join(mem_parts))

    # 注入已收集槽位（订单号/工单号等）：工具仅返回 success 无 data 时，
    # 模型仍能依据槽位在回复中点明具体单号/金额，避免含糊的"操作成功"。
    _slot_labels = {
        "order_id": "订单号",
        "ticket_id": "工单号",
        "tracking_number": "运单号",
        "refund_reason": "退款原因",
        "complaint_type": "投诉类型",
        "refund_preview": "退款预览",
    }
    slot_parts = []
    slot_context = state.business_context()
    for key, label in _slot_labels.items():
        value = slot_context.get(key)
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False)[:200]
        else:
            text = str(value)[:120]
        slot_parts.append(f"{label}：{text}")
    if slot_parts:
        parts.append("【已收集信息】" + "；".join(slot_parts))

    failed_tools = [tc for tc in state.tool_calls if not tc.success]
    if failed_tools:
        parts.append("【工具执行失败】")
        for tc in failed_tools:
            parts.append(f"  - {tc.tool_name}: {tc.error_message or '未知错误'}")

    kb_results = state.current_evidence()
    from app.agent.validation_llm import format_review_evidence
    parts.append("【知识库检索结果与工具执行结果】")
    parts.append(format_review_evidence(kb_results, self._tool_evidence_text(state)))

    parts.append("")
    parts.append("请根据以上信息，用自然、专业、简洁的中文回复用户。回复要点：")
    parts.append("1. 直接回应用户的问题或需求")
    parts.append("2. 如果有工具执行结果，清晰告知用户结果")
    parts.append("3. 如果有知识库内容，基于检索内容给出准确回答")
    parts.append("4. 保持友好专业的语气，适当引导下一步操作")
    parts.append("5. 回复控制在 200 字以内")
    if kb_results:
        parts.append("6. 仅根据【知识库检索结果】作答：每条关键论断都应有检索片段作为依据；"
                     "若证据不足，grounded 设为 false、refuse_recommended 设为 true，不要臆造。")

    return "\n".join(parts)


def build_context_messages(self: "ResponseContext", state: AgentState) -> list:
    """构建对话历史上下文"""
    context = []
    for msg in state.messages[-6:]:
        if msg.get("role") in ("user", "assistant"):
            context.append({
                "role": msg["role"],
                "content": msg.get("content", ""),
            })
    return context


def is_smalltalk(message: str) -> bool:
    """判断消息是否为高频寒暄语（问候/感谢/告别）。命中则走固定模板，不调 LLM。"""
    if not message:
        return False
    msg = message.strip().lower()
    return any(
        keyword in msg
        for group in SMALLTALK_PATTERNS.values()
        for keyword in group
    )


def get_default_reply(self: "ResponseContext", intent: str, message: str) -> str:
    if intent == IntentType.GENERAL:
        msg = (message or "").strip().lower()
        if any(g in msg for g in SMALLTALK_PATTERNS["greeting"]):
            return "您好，请问有什么可以帮您？"
        if any(t in msg for t in SMALLTALK_PATTERNS["thanks"]):
            return "不客气，还有其他可以帮您的吗？"
        if any(f in msg for f in SMALLTALK_PATTERNS["farewell"]):
            return "再见，祝您生活愉快，有需要随时找我。"
        return "我可以帮助您查询订单、处理退换货、解答产品问题等。请问有什么可以帮您的？"
    default_replies = {
        IntentType.QUERY_ORDER: "请提供您的订单号，我可以帮您查询最新的订单状态和物流信息。",
        IntentType.REFUND: "关于退换货问题，请提供订单号和退换货原因，我将为您处理。",
        IntentType.COMPLAINT: "非常抱歉给您带来不好的体验。请详细描述您遇到的问题，我们会尽快为您解决。",
        IntentType.TECHNICAL: "我来帮您解决这个问题。请详细描述遇到的情况，包括操作步骤和报错信息。",
        IntentType.PROMOTION: "我们目前有多种优惠活动。您可以在官网或 APP 首页查看最新的促销信息。",
    }
    return default_replies.get(intent, "我可以帮助您查询订单、处理退换货、解答产品问题等。请问有什么可以帮您的？")


class ResponseNode:
    """回答编排组件，服务与可替代生成器显式注入，最终审查不可被生成器绕过。"""

    def __init__(self, services: ResponseServices, *,
                 generate: Callable[[AgentState], Awaitable[str | None]] | None = None,
                 tool_reply: Callable[[AgentState], str | None] | None = None):
        self.services = services
        self.generate = generate
        self.tool_reply = tool_reply
        self.llm_runnable = RunnableLambda(self._invoke_llm_service)

    @property
    def llm_service(self):
        return self.services.model_factory()

    @property
    def validator(self):
        return self.services.validator_factory()

    @property
    def llm_judge(self):
        return self.services.judge_factory()

    @property
    def _answer_cache(self):
        return self.services.cache_factory()

    @property
    def knowledge_factory(self):
        return self.services.knowledge_factory

    @property
    def memory_factory(self):
        return self.services.memory_factory

    async def __call__(self, state: AgentState) -> str:
        return await response_generation_node(self, state)

    async def _generate_response_with_llm(self, state: AgentState) -> str | None:
        if self.generate is not None:
            return await self.generate(state)
        return await generate_response_with_llm(self, state)

    def _try_templated_tool_reply(self, state: AgentState) -> str | None:
        if self.tool_reply is not None:
            return self.tool_reply(state)
        return try_templated_tool_reply(self, state)

    _ANSWER_CACHE_TTL = ANSWER_CACHE_TTL
    _EXPLAIN_MARKERS = EXPLAIN_MARKERS
    _STRONG_EMOTION_MARKERS = STRONG_EMOTION_MARKERS
    _clean_chunk = staticmethod(clean_chunk)
    _build_llm_prompt = build_llm_prompt
    _build_context_messages = build_context_messages
    _add_rag_citations = add_rag_citations
    _answer_cache_get = answer_cache_get
    _answer_cache_set = answer_cache_set
    _build_answer_cache_key = build_answer_cache_key
    _get_default_reply = get_default_reply
    _build_order_reply_template = staticmethod(build_order_reply_template)
    _try_action_reply_template = try_action_reply_template
    _build_refund_reply_template = staticmethod(build_refund_reply_template)
    _build_ticket_status_reply_template = staticmethod(build_ticket_status_reply_template)
    _build_escalate_reply_template = staticmethod(build_escalate_reply_template)
    _rag_judge_applicable = rag_judge_applicable
    _tool_evidence_text = staticmethod(tool_evidence_text)
    _invoke_llm_service = invoke_llm_service
