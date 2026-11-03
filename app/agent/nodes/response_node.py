"""响应生成节点：LLM 生成、回答缓存、工具模板、RAG 引用与 LLM judge 校验。

自 graph.py 拆分迁移（原 AgentGraph._node_response_generation 及其全部私有助手），
逻辑保持原样。寒暄词表、回答缓存 TTL、动作模板标记等常量随本模块一并迁出，
并通过 graph.py 的类属性绑定供 `self.xxx` 访问。
"""
import json
import hashlib
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
from datetime import datetime

from app.agent.state import AgentState, AgentNode, IntentType
from app.config.config import settings
from app.services.user_memory import get_user_memory_service

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


async def validate_candidate(self: "AgentGraph", state: AgentState) -> bool:
    """The same gate checks every generated draft, including regeneration."""
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
    if self._rag_judge_applicable(state) and not state.collected_info.get("answer_from_cache"):
        judgement = await self.llm_judge.judge(
            response=body, user_query=state.user_message,
            kb_context=state.collected_info.get("kb_results", []),
            tool_evidence=self._tool_evidence_text(state),
        )
        if judgement is not None:
            state.collected_info["llm_judge"] = judgement
            refused = bool(
                judgement.get("refuse_recommended")
                or judgement.get("has_unsupported_claim")
                or (not judgement.get("grounded") and
                    judgement.get("fact_score", 0) < self.llm_judge.DEFAULT_THRESHOLD)
            )
            passed = passed and not refused
            report["judge_passed"] = not refused
            report["evidence_score"] = judgement.get("fact_score", 0)
            if refused:
                report["overall_score"] = min(report["overall_score"], judgement.get("fact_score", 0))
                report["issues"].append({"type": "evidence_rejected", "severity": "error"})
            state.collected_info["rag_refused"] = refused
    report["passed"] = passed
    state.collected_info["validation"] = report
    state.collected_info["answer_validated"] = passed
    return passed


async def response_generation_node(self: "AgentGraph", state: AgentState) -> str:
    if state.need_human and not state.reply:
        state.reply = f"非常抱歉，正在为您转接人工客服。{state.human_reason or ''}"
    if state.collected_info.get("smalltalk") and not state.reply:
        state.reply = self._get_default_reply(IntentType.GENERAL, state.user_message)

    has_business_tool = any(tc.tool_name != "hybrid_search" for tc in state.tool_calls)
    grounded = bool(state.collected_info.get("rag_grounded") and state.collected_info.get("kb_results"))
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
                state.collected_info["user_memory"] = await get_user_memory_service().get_user_profile(
                    user_id=state.user_id, query=state.user_message, session_id=state.session_id,
                )
            except Exception:
                state.collected_info["user_memory"] = {"memory_source": "degraded"}
        if grounded and not has_business_tool:
            answer_cache_key = self._build_answer_cache_key(state)
            cached = await self._answer_cache_get(answer_cache_key)
            if cached is not None:
                state.reply = cached
                state.collected_info["answer_from_cache"] = True
        if not state.reply:
            state.reply = await self._generate_response_with_llm(state)
            generated_by_llm = bool(state.reply)
            if not state.reply:
                state.reply = self._get_default_reply(state.detected_intent, state.user_message)

    # Workflow prompts and completed handoffs are terminal messages, not drafts.
    handoff_done = bool(state.collected_info.get("handoff_attempted")) or any(
        tc.tool_name == "escalate_to_human" for tc in state.tool_calls
    )
    workflow_reply = bool(
        state.missing_slots or state.needs_clarification
        or state.collected_info.get("smalltalk")
        or state.collected_info.get("rag_grounded") is False or handoff_done
    )
    if not workflow_reply:
        passed = await validate_candidate(self, state)
        if not passed and generated_by_llm and not state.collected_info.get("rag_refused"):
            # A replacement must pass the entire gate again, including the judge.
            state.collected_info.pop("inline_self_check", None)
            retry_reply = await self._generate_response_with_llm(state)
            if retry_reply:
                state.reply = retry_reply
                passed = await validate_candidate(self, state)
        if state.collected_info.get("rag_refused"):
            state.reply = (
                "抱歉，我暂时没有足够可靠的依据回答这个问题。"
                "为避免误导，建议您补充更具体的信息，或回复“转人工”由客服协助确认。"
            )
        elif not passed:
            state.need_human = True
            state.human_reason = "生成后回答校验未通过"
            return AgentNode.HUMAN_HANDOFF

    if grounded and not state.collected_info.get("rag_refused") and not handoff_done:
        state.reply = self._add_rag_citations(state.reply, state.collected_info["kb_results"])
    if (generated_by_llm and answer_cache_key and state.collected_info.get("answer_validated")
            and not state.need_human and not state.collected_info.get("rag_refused")
            and not state.collected_info.get("llm_governance", {}).get("degraded")):
        await self._answer_cache_set(answer_cache_key, state.reply)

    state.messages.append({
        "role": "assistant", "content": state.reply, "intent": state.detected_intent,
        "tool_calls": [tc.tool_name for tc in state.tool_calls],
        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
    })
    # Buffer provider tokens until the final answer has passed the gate.
    from app.agent.graph import _stream_event_queue
    stream_queue = _stream_event_queue.get()
    if stream_queue is not None:
        for offset in range(0, len(state.reply), 64):
            await stream_queue.put(("token", state.reply[offset:offset + 64]))
    return AgentNode.END


def add_rag_citations(self: "AgentGraph", reply: str, results: list[dict[str, Any]]) -> str:
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


def build_answer_cache_key(self: "AgentGraph", state: AgentState) -> str:
    """Scope reuse to the user, semantic context, current evidence and policy."""
    evidence = [
        {key: item.get(key) for key in ("id", "title", "content", "source", "version")}
        for item in state.collected_info.get("kb_results", [])[:3]
    ]
    payload = {
        "user_id": state.user_id, "intent": state.detected_intent,
        "query": state.rewritten_query or state.user_message,
        "prompt": self._build_llm_prompt(state),
        "history": [{"role": m.get("role"), "content": m.get("content")} for m in state.messages],
        "summary": state.conversation_summary, "evidence": evidence,
        "versions": [settings.LLM_PROMPT_VERSION, settings.LLM_MODEL_VERSION,
                     settings.LLM_SCHEMA_VERSION, settings.LLM_ROUTE_VERSION],
        "model": [settings.LLM_API_BASE, settings.LLM_MODEL, settings.LLM_BACKUP_BASE,
                  settings.LLM_BACKUP_MODEL, settings.LLM_THINKING_MODE,
                  settings.LLM_STRUCTURED_OUTPUT_METHOD],
        "judge": settings.LLM_JUDGE_ENABLED,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return f"validated-v2:{digest}"


async def answer_cache_get(self: "AgentGraph", key: str) -> Optional[str]:
    value = await self._answer_cache.aget(key)
    if isinstance(value, dict) and value.get("schema") == 2 and value.get("validated") is True:
        reply = value.get("reply")
        return reply if isinstance(reply, str) and reply else None
    return None


async def answer_cache_set(self: "AgentGraph", key: str, reply: str):
    await self._answer_cache.aset(
        key, {"schema": 2, "validated": True, "reply": reply}, ttl=self._ANSWER_CACHE_TTL,
    )


def try_templated_tool_reply(self: "AgentGraph", state: AgentState) -> Optional[str]:
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
    self: "AgentGraph", state: AgentState, tool_name: str, data: Optional[Dict[str, Any]]
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


def rag_judge_applicable(self: "AgentGraph", state: AgentState) -> bool:
    """All grounded policy answers use the gate, regardless of their intent label."""
    return (
        bool(state.collected_info.get("rag_grounded"))
        and not state.missing_slots
        and bool(state.collected_info.get("kb_results"))
    )


def tool_evidence_text(state: AgentState) -> str:
    """把成功工具返回的数据压缩成给 judge 的证据文本。"""
    parts = []
    for tc in state.tool_calls:
        if tc.success and isinstance(tc.result, dict) and tc.result.get("data"):
            parts.append(json.dumps(tc.result["data"], ensure_ascii=False)[:300])
    return "\n".join(parts)


async def generate_response_with_llm(self: "AgentGraph", state: AgentState) -> str:
    """使用 LLM 生成响应，失败时返回 None 由调用方降级"""
    # 延迟导入避免循环依赖：graph.py 在导入期加载本模块，此刻 graph 尚未
    # 完全初始化；运行期（生成响应时）graph 已加载完毕，导入恒安全。
    from app.agent.graph import _stream_event_queue

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
            async for chunk in self.llm_service.stream_chat(**payload):
                chunks.append(chunk)
            reply = "".join(chunks)
            metadata = self.llm_service.get_last_call_metadata()
            token_count = int(metadata.get("tokens", max(1, len(reply) // 4)))
        else:
            # chat() 返回的 intent 仅供日志/元数据展示；路由意图唯一由
            # intent.py 规则 + classify_intent 兜底决定，不再回写 state。
            reply, _model_intent, token_count, metadata = await self.llm_runnable.ainvoke(payload)
        state.total_tokens += token_count
        # 治理元数据只包含摘要、版本和预算结果，不保存发送给模型的敏感原文。
        state.collected_info["llm_governance"] = metadata

        logger.info(
            f"LLM 响应生成成功: intent={state.detected_intent}, tokens={token_count}, "
            f"session={state.session_id}"
        )
        return reply
    except Exception as e:
        logger.warning(f"LLM 调用失败: {e}")
        return None


async def invoke_llm_service(self: "AgentGraph", payload: Dict[str, Any]):
    reply, intent, tokens = await self.llm_service.chat(**payload)
    # RunnableLambda executes in its own context. Return metadata explicitly so
    # task-local model information travels with this exact answer.
    return reply, intent, tokens, self.llm_service.get_last_call_metadata()


def build_llm_prompt(self: "AgentGraph", state: AgentState) -> str:
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
    for key, label in _slot_labels.items():
        value = state.collected_info.get(key)
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False)[:200]
        else:
            text = str(value)[:120]
        slot_parts.append(f"{label}：{text}")
    if slot_parts:
        parts.append("【已收集信息】" + "；".join(slot_parts))

    successful_tools = [tc for tc in state.tool_calls if tc.success]
    if successful_tools:
        parts.append("【工具执行结果】")
        for tc in successful_tools:
            result = tc.result
            if isinstance(result, dict) and result.get("data"):
                data = result["data"]
                if isinstance(data, dict):
                    parts.append(f"  - {tc.tool_name}: {json.dumps(data, ensure_ascii=False, indent=2)}")
                elif isinstance(data, list) and data:
                    if isinstance(data[0], dict):
                        parts.append(f"  - {tc.tool_name}: 找到 {len(data)} 条结果")
                        for item in data[:3]:
                            title = item.get("title", "")
                            content = item.get("content", "")[:200]
                            if title:
                                parts.append(f"    【{title}】{content}")
                    else:
                        parts.append(f"  - {tc.tool_name}: {json.dumps(data, ensure_ascii=False)[:500]}")
                else:
                    parts.append(f"  - {tc.tool_name}: {str(data)[:500]}")
            elif isinstance(result, dict) and result.get("success"):
                parts.append(f"  - {tc.tool_name}: 操作成功")
            else:
                parts.append(f"  - {tc.tool_name}: 执行成功")

    failed_tools = [tc for tc in state.tool_calls if not tc.success]
    if failed_tools:
        parts.append("【工具执行失败】")
        for tc in failed_tools:
            parts.append(f"  - {tc.tool_name}: {tc.error_message or '未知错误'}")

    kb_results = state.collected_info.get("kb_results", [])
    if kb_results:
        parts.append("【知识库检索结果】")
        for i, result in enumerate(kb_results[:3], 1):
            title = result.get("title", "")
            content = result.get("content", "")
            category = result.get("category", "")
            score = result.get("final_score", result.get("hybrid_score", 0))
            parts.append(f"  {i}. 【{title}】({category}, 相关度:{score:.0%})")
            if content:
                clean_title, chapter, body = self._clean_chunk(str(content), title)
                if chapter:
                    parts.append(f"     章节：{chapter}")
                if body:
                    parts.append(f"     {body[:300]}")

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


def build_context_messages(self: "AgentGraph", state: AgentState) -> list:
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


def get_default_reply(self: "AgentGraph", intent: str, message: str) -> str:
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
