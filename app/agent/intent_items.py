"""逐项需求、当前执行观察与回答覆盖；进度描述不构成证据或执行许可。"""
from __future__ import annotations

import hashlib
import json
import re
from contextvars import ContextVar
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.agent.state import AgentState

VERSION = "intent-items.v1"
REVIEW_CONTEXT: ContextVar[dict | None] = ContextVar("intent_item_review", default=None)
REVIEW_PROMPT = """
逐项检查item_checks，必须每个i编号恰好一次。回答须有对应【i编号】段落，answer_quote逐字引用该段正文。
supported判断该项回答是否忠实于限定来源；conditions_preserved检查必要条件、否定、例外与执行阶段，不能只匹配关键词。
answerability独立判断该项现有证据能否完整回答；存在资料不等于支持该问题，无法回答时允许limited并准确说明未知。
answerable却只补问或拒答属于不必要限制。遗漏/答非所问标missing或supported=false，不能用其他事项的正确答案代替。
evidence_ids只用该项允许的当前K/T/P身份。没有引用不能answered；准备退款只能awaiting_confirmation，不是提交或退款成功。
状态描述诉求的实际完成阶段，不是“解释是否完整”。条件未满足、订单不可用或条件未知而未办理的prepare_refund项，必须status=limited、answerability=partial或unanswerable；即使已完整解释原因也不能answered。
只有当前P1预览存在时prepare_refund才可awaiting_confirmation；unsupported项也只能limited，说明未执行而非answered。
waiting_feedback仅表示排障步骤等待结果；补问订单号、缺参数、权限不足或无资料均为limited，不是waiting_feedback。
排障建议不表示问题解决；需继续验证时waiting_feedback，并引用当前知识。只有本轮用户明确报告解决才可answered，并引用U1。
"""
ItemKind = Literal["query_order", "get_user_history", "knowledge", "troubleshoot", "prepare_refund", "unsupported"]
ItemStatus = Literal["pending", "answered", "limited", "waiting_feedback", "awaiting_confirmation", "submitted", "cancelled"]


class IntentItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    text: str = Field(min_length=1, max_length=300)
    kind: ItemKind


class PlannedItem(IntentItem):
    item_id: str = Field(pattern=r"^(i[1-4]|policy)$")
    source_message_id: int | None = None
    source_scope: str = ""


class ItemObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    scope: str
    attempted: bool
    references: list[str] = Field(default_factory=list, max_length=12)
    error_code: str | None = None


class ItemVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    item_id: str = Field(pattern=r"^i[1-4]$")
    status: Literal["answered", "limited", "waiting_feedback", "awaiting_confirmation", "missing"]
    answer_quote: str = Field(max_length=180)
    evidence_ids: list[str] = Field(max_length=6)
    supported: bool
    conditions_preserved: bool
    answerability: Literal["answerable", "partial", "unanswerable", "unknown"]


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def active(state: AgentState) -> bool:
    return bool(state.task and state.task.items and state.task_runtime)


def canonical_item_markers(state: AgentState,text: str) -> str:
    """Canonicalize unambiguous presentation labels before independent review only."""
    if not active(state) or re.search(r'【i\d+】',text): return text
    expected=[item.item_id[1:] for item in state.task.items]
    labels=re.findall(r'【(\d+)】',text)
    line_labels=re.findall(r'(?m)^[ \t]*【(\d+)】',text)
    if labels!=expected or line_labels!=expected: return text
    return re.sub(r'(?m)^([ \t]*)【([1-4])】',lambda match:match[1]+'【i'+match[2]+'】',text)


def validate_plan(state: AgentState, value) -> list[PlannedItem]:
    """只接受当前可见原话；承接任务不得删减或替换原需求项。"""
    raw = getattr(value, "items", [])
    if getattr(value, "task_kind", "single") != "multi":
        if len(raw)>1:
            raise ValueError("items_require_multi_task")
        requests={(value.topic,value.request_type),*[(entry.topic,entry.request_type) for entry in value.subrequests]}
        if len(requests)>1:
            raise ValueError("multiple_registered_requests_require_items")
        if raw:
            item=raw[0]
            if not item.text.strip() or item.text not in state.user_message:
                raise ValueError("intent_item_not_from_current_message")
            allowed={item.kind}
            if item.kind=="prepare_refund": allowed.add("query_order")
            if any(read.tool not in allowed for read in value.read_tasks):
                raise ValueError("read_request_omitted_from_items")
            if value.topic=="query_order" and value.request_type=="query" and item.kind!="query_order":
                raise ValueError("primary_order_omitted_from_items")
        return []
    if not 2 <= len(raw) <= 4:
        raise ValueError("multi_task_requires_individual_items")
    from app.agent.request_context import request_scope_digest
    plan = [PlannedItem(**item.model_dump(), item_id=f"i{index+1}", source_message_id=state.source_message_id,
                        source_scope=request_scope_digest(state)) for index, item in enumerate(raw)]
    if len({re.sub(r"[\s，,。；;！？!?]", "", item.text) for item in plan}) != len(plan):
        raise ValueError("duplicate_intent_item")
    continuing = bool(value.continue_task and state.task and state.task.status == "waiting_input")
    if continuing and state.task.items:
        if (state.task.user_id != state.user_id or state.task.session_id != state.session_id
                or state.task.actor_id != (state.actor_id or state.user_id)
                or [(i.item_id, i.kind, i.text) for i in plan] != [(i.item_id, i.kind, i.text) for i in state.task.items]):
            raise ValueError("continued_items_changed")
        return list(state.task.items)
    from app.agent.routing_policy import action_is_negated
    kinds = {item.kind for item in plan}
    for item in plan:
        if not item.text.strip() or item.text not in state.user_message:
            raise ValueError("intent_item_not_from_current_message")
        if item.kind == "get_user_history" and not any(r.tool == item.kind for r in value.read_tasks):
            raise ValueError("item_history_not_requested")
        if item.kind == "prepare_refund":
            from app.agent.routing_policy import is_conditional
            requests = [(value.topic, value.request_type), *[(r.topic, r.request_type) for r in value.subrequests]]
            if ("refund", "apply") not in requests or action_is_negated("refund", state.user_message):
                raise ValueError("item_refund_not_requested")
            if is_conditional(state.user_message) and not value.conditions:
                raise ValueError("item_refund_condition_missing")
    if value.read_tasks and any(read.tool not in kinds for read in value.read_tasks):
        raise ValueError("read_request_omitted_from_items")
    if value.topic == "query_order" and value.request_type == "query" and "query_order" not in kinds:
        raise ValueError("primary_order_omitted_from_items")
    if any(r.topic == "query_order" and r.request_type == "query" for r in value.subrequests) and "query_order" not in kinds:
        raise ValueError("order_subrequest_omitted_from_items")
    if any(topic == "refund" and kind == "apply" for topic, kind in
           [(value.topic, value.request_type), *[(r.topic, r.request_type) for r in value.subrequests]]) and "prepare_refund" not in kinds:
        raise ValueError("refund_subrequest_omitted_from_items")
    return plan


def catalog(state: AgentState) -> dict[str, dict]:
    """短引用身份绑定完整当前来源；模型不能制造新来源。"""
    result = {}
    from app.agent.request_context import request_scope_digest
    scope=request_scope_digest(state)
    for item in state.task.items:
        observation=state.task_runtime.item_observations.get(item.item_id) if state.task_runtime else None
        if not observation or observation.scope!=scope or not observation.attempted:
            continue
        if item.kind=='query_order' and not state.task.order_id and observation.error_code=='missing_order_id':
            data={'item_id':item.item_id,'error_code':'missing_order_id','order_id_available':False,
                'lookup_executed':False,'grants_authorization':False}
        elif item.kind in {'knowledge','troubleshoot'} and observation.error_code=='no_item_evidence':
            data={'item_id':item.item_id,'error_code':'no_item_evidence','query_text':item.text,
                'retrieval_attempted':True,'current_query_has_evidence':False,
                'does_not_prove_global_absence':True,'grants_authorization':False}
        else: continue
        result['L'+item.item_id[1:]]={'kind':'current_information_limit','digest':_digest([scope,data]),'data':data}
    if all(call.tool_name in {'query_order','get_user_history','hybrid_search'} for call in state.tool_calls):
        from app.agent.request_context import request_scope_digest
        from app.agent.routing_policy import action_is_negated
        for item in state.task.items:
            if item.kind=='unsupported':
                data={'item_id':item.item_id,'requested_text':item.text,'supported_by_current_flow':False,
                    'operation_executed':False,'grants_authorization':False}
                result['E'+item.item_id[1:]]={'kind':'capability_limit',
                    'digest':_digest([request_scope_digest(state),data]),'data':data}
            elif (item.kind=='knowledge' and not state.task.refund_requested
                  and action_is_negated('refund',item.text)):
                data={'item_id':item.item_id,'requested_text':item.text,'refund_requested':False,
                    'operation_executed':False,'grants_authorization':False}
                result['E'+item.item_id[1:]]={'kind':'current_read_only_execution',
                    'digest':_digest([request_scope_digest(state),data]),'data':data}
    if any(item.kind == "troubleshoot" for item in state.task.items):
        from app.agent.request_context import request_scope_digest
        result["U1"] = {"kind": "current_user_report", "digest": _digest([request_scope_digest(state), state.user_message]),
                        "data": {"text": state.user_message, "source": "current_message"}}
    for index, item in enumerate(state.current_evidence()):
        if not item.get("id") or not item.get("version_id") or not item.get("content"):
            continue
        result[f"K{index+1}"] = {"kind": "knowledge", "digest": _digest(item), "data": item}
    for item in state.task.items:
        observation=state.task_runtime.item_observations.get(item.item_id) if state.task_runtime else None
        if (item.kind not in {'knowledge','troubleshoot'} or not observation
                or observation.scope!=scope or not observation.attempted):
            continue
        sources=[key for key,entry in result.items() if entry['kind']=='knowledge' and entry['digest'] in observation.references]
        if sources:
            data={'item_id':item.item_id,'retrieval_attempted':True,'sources':sources,
                'does_not_prove_global_absence':True,'does_not_prove_user_eligibility':True,'grants_authorization':False}
            result['S'+item.item_id[1:]]={'kind':'current_evidence_scope',
                'digest':_digest([scope,data,[result[key]['digest'] for key in sources]]),'data':data}
    for index, call in enumerate(state.tool_calls):
        matches = call.arguments.get("user_id") == state.user_id
        if call.tool_name == "query_order":
            matches = matches and call.arguments.get("order_id") == state.task.order_id
        if call.tool_name == "get_user_history":
            matches = matches and call.arguments.get("session_id") == state.session_id
        if call.success and matches and call.tool_name in {"query_order", "get_user_history"}:
            result[f"T{index+1}"] = {"kind": call.tool_name, "digest": _digest(call.model_dump()), "data": call.result}
        elif (matches and call.tool_name in {'query_order','get_user_history'} and not call.success
              and isinstance(call.result,dict) and call.result.get('success') is False):
            error=call.result.get('error_type')
            if error not in {'permission','not_found','unavailable','timeout','validation'}: error='unknown'
            result[f'T{index+1}']={'kind':call.tool_name,'digest':_digest(call.model_dump()),
                'data':{'success':False,'data':None,'error_type':error}}
    preview = state.current_refund_preview()
    action = state.current_refund_action()
    if preview is not None and action is not None and action.matches(preview) and action.is_pending():
        data = {"action_id": preview.action_id, "order_id": preview.order_id,
                "stage": "awaiting_confirmation", "application_submitted": False}
        result["P1"] = {"kind": "prepare_refund", "digest": _digest(data), "data": data}
    return result


def observe(state: AgentState, item_id: str, references: list[str], error: str | None = None) -> None:
    from app.agent.request_context import request_scope_digest
    state.task_runtime.item_observations[item_id] = ItemObservation(
        scope=request_scope_digest(state), attempted=True, references=references, error_code=error)


def check_conditions(state: AgentState) -> None:
    from app.agent.task_loop import current_order, conditions_met
    if not active(state) or not state.task.refund_requested:
        return
    order = current_order(state)
    unavailable = not state.task.order_id or bool(state.tool_observations.get("query_order") and not order)
    if unavailable or order and state.understanding.conditions and not conditions_met(state):
        refs = [entry["digest"] for entry in catalog(state).values() if entry["kind"] == "query_order"]
        known = bool(order and order.get("status_code") in {"pending", "paid", "shipped", "delivered", "completed", "cancelled"})
        for item in state.task.items:
            if item.kind == "prepare_refund":
                observe(state, item.item_id, refs, "order_unavailable" if unavailable else "condition_not_satisfied" if known else "condition_unknown")


def pending_knowledge(state: AgentState) -> list[PlannedItem]:
    if not active(state):
        return []
    pending = [item for item in state.task.items if item.kind in {"knowledge", "troubleshoot"}
               and item.item_id not in state.task_runtime.item_observations]
    blocked = any(value.error_code in {"condition_not_satisfied", "condition_unknown", "order_unavailable"}
                  for value in state.task_runtime.item_observations.values())
    if state.task.refund_requested and not blocked and "policy" not in state.task_runtime.item_observations:
        # Internal precondition, not an extra user goal or authorization.
        pending.append(PlannedItem(item_id="policy", text=state.task.goal[:300], kind="knowledge"))
    return pending


def evidence_digest(value: dict) -> str:
    return _digest(value)


def diagnostic_resolution_reported(message: str) -> bool:
    return bool(re.search(r"问题(?:已经|已)?解决了|故障(?:已经|已)?解决|现在能用了|现在可以正常使用", message)) and not re.search(
        r"没有|并未|还没|是否|如果|吗|以前|之前|曾经|据说|客服说|[“”\"？?]", message)


def execution_gaps(state: AgentState) -> list[str]:
    from app.agent.request_context import request_scope_digest
    references = {entry["digest"] for entry in catalog(state).values()}
    gaps = []
    for item in state.task.items:
        observation = state.task_runtime.item_observations.get(item.item_id)
        if item.kind == "unsupported":
            continue
        if not observation or observation.scope != request_scope_digest(state) or not observation.attempted:
            gaps.append(item.item_id + ":not_attempted")
        elif not set(observation.references).issubset(references):
            gaps.append(item.item_id + ":stale_evidence")
    return gaps


def snapshot(state: AgentState) -> dict | None:
    if not active(state):
        return None
    from app.agent.request_context import request_scope_digest
    if (state.task.user_id != state.user_id or state.task.session_id != state.session_id
            or state.task.actor_id != (state.actor_id or state.user_id)
            or state.task_runtime.scope_digest != request_scope_digest(state)):
        return {"version": VERSION, "scope_valid": False, "items": [], "catalog": {}}
    entries = catalog(state)
    items = []
    from app.agent.request_context import request_scope_digest
    for item in state.task.items:
        observation = state.task_runtime.item_observations.get(item.item_id)
        current = bool(observation and observation.scope == request_scope_digest(state))
        allowed = [key for key, entry in entries.items()
                   if current and entry["digest"] in observation.references]
        allowed.extend(key for key,entry in entries.items() if entry['kind'] in {'capability_limit','current_read_only_execution','current_information_limit','current_evidence_scope'}
            and entry['data']['item_id']==item.item_id)
        items.append({**item.model_dump(), "attempted": bool(current and observation.attempted),
            "evidence_ids": allowed, "error_code": observation.error_code if current else None})
    public_catalog = {key: {"kind": entry["kind"], "digest": entry["digest"],
        **({"source_id": entry["data"].get("id"), "version_id": entry["data"].get("version_id")}
           if entry["kind"] == "knowledge" else {}),
        **({"preview": entry["data"]} if entry["kind"] == "prepare_refund" else {}),
        **({"user_report": entry["data"]} if entry["kind"] == "current_user_report" else {}),
        **({'operation_limit':entry['data']} if entry['kind']=='capability_limit' else {}),
        **({'read_only_execution':entry['data']} if entry['kind']=='current_read_only_execution' else {}),
        **({'information_limit':entry['data']} if entry['kind']=='current_information_limit' else {}),
        **({'evidence_scope':entry['data']} if entry['kind']=='current_evidence_scope' else {}),
        **({"review_tool": {"tool":entry["kind"],"success":True,"data":entry["data"]["data"]}}
           if entry["kind"] in {"query_order","get_user_history"} and isinstance(entry["data"],dict)
           and entry["data"].get("success") is True and entry["data"].get("data") else {}),
        **({'review_failure':{'tool':entry['kind'],'success':False,'data':None,'error_type':entry['data']['error_type']}}
           if entry['kind'] in {'query_order','get_user_history'} and isinstance(entry['data'],dict)
           and entry['data'].get('success') is False else {})}
        for key, entry in entries.items()}
    return {"version": VERSION, "items": items, "catalog": public_catalog,
            "rule": "逐项回答，不能用一个成功代替其余事项；历史进度不是本轮证据或授权。"}


def verify_review(state: AgentState, response: str, raw: object) -> tuple[list[ItemVerdict], list[str]]:
    """核对完整覆盖、原文段落、当前来源及状态；语义支持判断来自独立Judge。"""
    context = snapshot(state)
    if context is None:
        return [], []
    if context.get("scope_valid") is False:
        return [], ["item_scope_changed"]
    try:
        if not isinstance(raw, list):
            raise ValueError("missing_item_review")
        verdicts = [ItemVerdict.model_validate(item) for item in raw]
        by_id = {item.item_id: item for item in verdicts}
        if len(by_id) != len(verdicts) or set(by_id) != {item.item_id for item in state.task.items}:
            raise ValueError("incomplete_or_duplicate_item_review")
    except (ValueError, TypeError):
        return [], ["item_review_invalid"]
    issues = []
    markers = re.findall(r"【(i\d+)】", response)
    if len(markers) != len(context["items"]) or set(markers) != set(by_id):
        issues.append("item_sections_invalid")
    for item in context["items"]:
        verdict = by_id[item["item_id"]]
        marker = f"【{item['item_id']}】"
        match = re.search(re.escape(marker) + r"(.*?)(?=【i[1-4]】|\Z)", response, re.S)
        if (match is None or not verdict.answer_quote.strip() or verdict.answer_quote not in match.group(0)
                or verdict.status == "missing"):
            issues.append(item["item_id"] + ":answer_missing")
        if not verdict.supported or not verdict.conditions_preserved:
            issues.append(item["item_id"] + ":semantic_rejected")
        if not set(verdict.evidence_ids).issubset(item["evidence_ids"]):
            issues.append(item["item_id"] + ":foreign_evidence")
        if verdict.status == "answered" and (not verdict.evidence_ids or item["kind"] in {"unsupported", "prepare_refund"}):
            issues.append(item["item_id"] + ":unproven_completion")
        if verdict.status == "answered" and verdict.answerability != "answerable":
            issues.append(item["item_id"] + ":insufficient_support")
        if verdict.status=='answered' and verdict.evidence_ids and all(
                context['catalog'].get(ref,{}).get('kind')=='current_evidence_scope' for ref in verdict.evidence_ids):
            issues.append(item['item_id']+':scope_manifest_not_business_evidence')
        if item["kind"] == "troubleshoot" and verdict.status == "answered" and (
                not diagnostic_resolution_reported(state.user_message) or "U1" not in verdict.evidence_ids):
            issues.append(item["item_id"] + ":diagnosis_not_resolved")
        if verdict.status == "waiting_feedback" and (item["kind"] != "troubleshoot"
                or not any(ref.startswith("K") for ref in verdict.evidence_ids)):
            issues.append(item["item_id"] + ":diagnostic_step_not_supported")
        if verdict.status == "awaiting_confirmation" and (item["kind"] != "prepare_refund" or "P1" not in verdict.evidence_ids):
            issues.append(item["item_id"] + ":preview_not_current")
        if item["kind"] == "prepare_refund" and "P1" in item["evidence_ids"] and verdict.status != "awaiting_confirmation":
            issues.append(item["item_id"] + ":preview_stage_omitted")
        if verdict.status == "limited" and verdict.answerability == "answerable":
            issues.append(item["item_id"] + ":unnecessary_limitation")
    return verdicts, sorted(set(issues))


def finalize(state: AgentState) -> None:
    if not active(state):
        return
    from app.agent.task_loop import progress
    reviews = state.task_runtime.item_reviews
    outcomes = {entry.item_id: entry.status for entry in reviews}
    if not reviews:
        return
    if any(value == "awaiting_confirmation" for value in outcomes.values()):
        progress(state, item_outcomes=outcomes, status="waiting_confirmation", waiting_for="refund_confirmation")
    elif all(value == "answered" for value in outcomes.values()):
        progress(state, item_outcomes=outcomes, status="completed", stop_reason="all_items_answered", waiting_for=None)
    else:
        progress(state, item_outcomes=outcomes, status="waiting_input", stop_reason="items_partially_answered",
                 waiting_for="diagnostic_feedback" if "waiting_feedback" in outcomes.values() else "item_follow_up")
    from app.agent.task_context import remember_task_turn
    for entry in reviews:
        if entry.status == "waiting_feedback":
            remember_task_turn(state, "suggested_step", entry.answer_quote)
