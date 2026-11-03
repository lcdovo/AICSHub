"""
Agent 状态定义 - 定义 Agent 在执行过程中需要追踪的所有状态
"""

from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field
from copy import deepcopy
from datetime import datetime
from app.agent.read_plan import ReadTask
from app.agent.flow_contracts import FlowFlag, TurnFlow
from app.agent.generation_contracts import GenerationMetadata
from app.agent.business_contracts import BusinessParameters
from app.agent.confirmation_contracts import TurnRefundConfirmation
from app.agent.refund_preview_contracts import RefundPreview
from app.agent.refund_action_contracts import RefundActionView
from app.agent.tool_contracts import ToolObservation, BusinessToolName, TOOL_RESULT_COMPAT_KEYS, TOOL_IDENTIFIER_PROJECTIONS


class ToolCall(BaseModel):
    """工具调用记录"""

    tool_name: str
    arguments: Dict[str, Any]
    result: Any = None
    success: bool = True
    error_message: Optional[str] = None
    error_type: Optional[str] = None


from app.agent.request_understanding import (
    RequestUnderstanding,
    ConversationContext,
    ActionIntent,
    ReviewResult,
    EvidenceBundle,
)


class AgentState(BaseModel):
    """Agent 运行时状态"""

    # 基本信息
    session_id: str
    user_id: int
    user_message: str
    session_version: Optional[int] = None
    understanding: Optional[RequestUnderstanding] = None
    conversation_context: Optional[ConversationContext] = None
    action_intent: Optional[ActionIntent] = None
    review_result: Optional[ReviewResult] = None
    evidence_bundle: Optional[EvidenceBundle] = None
    retrieval_metadata: Dict[str, Any] = Field(default_factory=dict)
    retrieval_error: Optional[str] = None
    understanding_mode: Optional[str] = None
    strategy_version: Optional[str] = None
    node_steps: int = 0
    flow: TurnFlow = Field(default_factory=TurnFlow)
    generation_metadata: Optional[GenerationMetadata] = None
    generation_metadata_error: Optional[str] = None
    business_parameters: BusinessParameters = Field(default_factory=BusinessParameters)
    business_parameter_errors: List[str] = Field(default_factory=list)
    refund_confirmation: Optional[TurnRefundConfirmation] = None
    refund_preview: Optional[RefundPreview] = None
    refund_preview_established: bool = False
    refund_preview_error: Optional[str] = None
    refund_action_view: Optional[RefundActionView] = None
    refund_action_established: bool = False
    refund_action_error: Optional[str] = None
    read_plan: List[ReadTask] = Field(default_factory=list)
    # 当前轮已落库的用户消息；不能从collected_info/历史或模型实体取得。
    source_message_id: Optional[int] = None
    actor_id: Optional[int] = None
    requested_action_id: Optional[str] = None
    requested_action_version: Optional[int] = None

    # 意图识别
    detected_intent: str = "general"
    intent_confidence: float = 0.0
    needs_clarification: bool = False

    # 对话历史
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    conversation_summary: Optional[str] = None

    # Agent 执行状态
    current_node: str = "start"
    tool_calls: List[ToolCall] = Field(default_factory=list)
    tool_observations: Dict[BusinessToolName, ToolObservation] = Field(default_factory=dict)
    missing_slots: List[str] = Field(default_factory=list)
    route_reason: Optional[str] = None
    # RAG 查询字段；user_message 始终保留用户原文
    rewritten_query: Optional[str] = None
    query_rewrite_applied: bool = False
    query_rewrite_reason: Optional[str] = None
    collected_info: Dict[str, Any] = Field(
        default_factory=dict
    )  # 已收集的信息（如订单号等）
    pending_confirmations: List[str] = Field(default_factory=list)  # 待确认项

    # 结果
    reply: str = ""
    response_ready: bool = False
    need_human: bool = False
    human_reason: Optional[str] = None

    # 追踪
    trace: List[Dict[str, Any]] = Field(default_factory=list)  # 执行链路追踪
    total_tokens: int = 0
    execution_time_ms: int = 0

    # 用户情绪
    user_emotion: str = "neutral"  # neutral, positive, negative, angry

    def flow_value(self, flag: FlowFlag) -> bool | None:
        if not isinstance(flag, FlowFlag):
            raise TypeError("flow_flag_enum_required")
        value = getattr(self.flow, flag.value)
        if flag.value in self.flow.model_fields_set:
            return value
        # 无类型结论的旧内部调用可一次性导入真实bool；兼容字典后续不能覆盖已立结论。
        legacy = self.collected_info.get(flag.value)
        if type(legacy) is bool:
            self.set_flow(**{flag.value: legacy})
            return legacy
        return None

    def record_generation_metadata(self, metadata: Dict[str, Any]) -> bool:
        try:
            parsed = GenerationMetadata.from_service_metadata(metadata)
        except (ValueError, TypeError):
            parsed = GenerationMetadata(degraded=True, degraded_reason="generation_metadata_invalid")
            self.generation_metadata_error = "generation_metadata_invalid"
            accepted = False
        else:
            self.generation_metadata_error = None
            accepted = True
        self.generation_metadata = parsed
        self.collected_info["llm_governance"] = parsed.model_dump(exclude_none=True)
        return accepted

    def current_generation_metadata(self) -> GenerationMetadata:
        if self.generation_metadata is None and "llm_governance" in self.collected_info:
            self.record_generation_metadata(self.collected_info["llm_governance"])
        return self.generation_metadata or GenerationMetadata()

    def set_flow(self, **values: bool | None) -> None:
        # 不使用model_copy(update=...)，它不会验证类型/额外键。
        self.flow = TurnFlow.model_validate({**self.flow.model_dump(exclude_unset=True), **values})
        for key, value in values.items():
            if value is None:
                self.collected_info.pop(key, None)
            else:
                self.collected_info[key] = value

    def set_business_parameters(self, **values: Any) -> None:
        parsed = BusinessParameters.model_validate({
            **self.business_parameters.model_dump(exclude_unset=True), **values,
        })
        self.business_parameters = parsed
        self.business_parameter_errors = [key for key in self.business_parameter_errors if key not in values]
        for key, value in values.items():
            if value is None:
                self.collected_info.pop(key, None)
            else:
                self.collected_info[key] = value

    def current_business_parameters(self) -> BusinessParameters:
        updates = {}
        invalid = []
        for key in BusinessParameters.model_fields:
            if key in self.business_parameters.model_fields_set or key not in self.collected_info:
                continue
            try:
                single = BusinessParameters.model_validate({key: self.collected_info[key]})
            except (ValueError, TypeError):
                updates[key] = None
                invalid.append(key)
            else:
                updates[key] = getattr(single, key)
        if updates:
            self.set_business_parameters(**updates)
        self.business_parameter_errors = sorted(set(self.business_parameter_errors) | set(invalid))
        return self.business_parameters

    def business_context(self) -> Dict[str, Any]:
        """兼容上下文投影按已建立的类型参数覆盖，不能用后改字典换掉选定订单。"""
        parsed = self.current_business_parameters()
        context = deepcopy(self.collected_info)
        for key in parsed.model_fields_set:
            value = getattr(parsed, key)
            if value is None:
                context.pop(key, None)
            else:
                context[key] = value
        preview = self.current_refund_preview()
        if self.refund_preview_established:
            if preview is None:
                context.pop("refund_preview", None)
            else:
                context["refund_preview"] = preview.model_dump(exclude_none=True)
        action = self.current_refund_action()
        if self.refund_action_established:
            if action is None:
                context.pop("refund_action", None)
            else:
                context["refund_action"] = action.model_dump(exclude_none=True)
        return context

    def merge_extracted_business_parameters(self, extracted: Dict[str, Any], *, model_selected_order: bool) -> bool:
        self.current_business_parameters()
        try:
            parsed = BusinessParameters.model_validate(extracted)
            changes = {}
            for key in parsed.model_fields_set:
                if key == "order_id" and model_selected_order:
                    continue
                if key == "order_id" or not getattr(self.business_parameters, key):
                    changes[key] = getattr(parsed, key)
            self.set_business_parameters(**changes)
        except (ValueError, TypeError):
            self.business_parameter_errors = sorted(set(self.business_parameter_errors) | {"extracted_parameters"})
            return False
        self.business_parameter_errors = [key for key in self.business_parameter_errors if key != "extracted_parameters"]
        return not self.business_parameter_errors

    def _refund_confirmation_digest(self) -> str:
        import hashlib
        import json
        action = self.current_refund_action()
        payload = {
            "session_id": self.session_id, "user_id": self.user_id, "actor_id": self.actor_id,
            "source_message_id": self.source_message_id, "message": self.user_message,
            "preview": self.refund_preview_data() or None,
            "action": action.model_dump(exclude_none=True) if action is not None else None,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

    def record_refund_confirmation(self, origin: str) -> None:
        from app.agent.refund_authorization import CONFIRMATIONS
        if origin == "current_message" and self.user_message.strip() not in CONFIRMATIONS:
            raise ValueError("current_message_not_confirmation")
        self.current_refund_preview()
        self.current_refund_action()
        if self.refund_preview_error or self.refund_action_error:
            raise ValueError("refund_preview_invalid")
        self.refund_confirmation = TurnRefundConfirmation(
            origin=origin, context_digest=self._refund_confirmation_digest()
        )
        self.collected_info["confirm_refund"] = True  # 仅兼容观察，不再是确认来源。

    def has_current_refund_confirmation(self) -> bool:
        return bool(self.refund_confirmation and
                    self.refund_confirmation.context_digest == self._refund_confirmation_digest())

    def set_refund_preview(self, raw: Dict[str, Any] | None) -> None:
        parsed = RefundPreview.model_validate(raw) if raw is not None else None
        if parsed is not None and (parsed.user_id not in (None, self.user_id)
                                   or parsed.session_id not in (None, self.session_id)):
            raise ValueError("refund_preview_scope_mismatch")
        self.refund_preview = parsed
        self.refund_preview_established = True
        self.refund_preview_error = None
        self.refund_confirmation = None
        self.collected_info.pop("confirm_refund", None)
        if parsed is None:
            self.collected_info.pop("refund_preview", None)
        else:
            self.collected_info["refund_preview"] = parsed.model_dump(exclude_none=True)

    def current_refund_preview(self) -> Optional[RefundPreview]:
        if self.refund_preview_established:
            if self.refund_preview is not None and (self.refund_preview.user_id not in (None, self.user_id)
                                                     or self.refund_preview.session_id not in (None, self.session_id)):
                self.refund_preview_error = "refund_preview_scope_changed"
                self.refund_confirmation = None
                return None
            if self.refund_preview is not None and self.collected_info.get("refund_preview") != self.refund_preview.model_dump(exclude_none=True):
                self.refund_preview_error = "refund_preview_projection_changed"
                self.refund_confirmation = None
                return None
            return self.refund_preview
        if "refund_preview" not in self.collected_info:
            return None
        try:
            self.set_refund_preview(self.collected_info["refund_preview"])
        except (ValueError, TypeError):
            self.refund_preview_established = True
            self.refund_preview_error = "refund_preview_invalid"
            self.refund_confirmation = None
        return self.refund_preview

    def refund_preview_data(self) -> Dict[str, Any]:
        parsed = self.current_refund_preview()
        return parsed.model_dump(exclude_none=True) if parsed is not None else {}

    def record_refund_action(self, raw: Dict[str, Any] | None) -> bool:
        self.refund_confirmation = None
        self.collected_info.pop("confirm_refund", None)
        self.refund_action_established = True
        self.refund_action_error = None
        if raw is None:
            self.refund_action_view = None
            self.collected_info.pop("refund_action", None)
            return True
        try:
            parsed = RefundActionView.model_validate(raw)
        except (ValueError, TypeError):
            self.refund_action_view = None
            self.refund_action_error = "refund_action_invalid"
            self.collected_info.pop("refund_action", None)
            return False
        self.refund_action_view = parsed
        self.collected_info["refund_action"] = parsed.model_dump(exclude_none=True)
        return True

    def current_refund_action(self) -> Optional[RefundActionView]:
        if self.refund_action_established:
            if self.refund_action_view is not None and self.collected_info.get("refund_action") != self.refund_action_view.model_dump(exclude_none=True):
                self.refund_action_error = "refund_action_projection_changed"
                self.refund_confirmation = None
                self.collected_info.pop("confirm_refund", None)
                return None
            return self.refund_action_view
        if "refund_action" not in self.collected_info:
            return None
        raw = self.collected_info["refund_action"]
        # 显式clear(None)是可信调用；历史投影中的null不是完整领域回执。
        self.record_refund_action(raw if raw is not None else {})
        return self.refund_action_view

    def current_evidence(self) -> List[Dict[str, Any]]:
        """类型证据是主链权威；兼容旧内部调用时仅在尚无bundle时读取旧字段。"""
        if self.evidence_bundle is not None:
            return deepcopy(self.evidence_bundle.items)
        return deepcopy(self.collected_info.get("kb_results", []))

    def record_tool_observation(self, tool_name: str, result: Dict[str, Any]) -> None:
        observation = ToolObservation.model_validate({**deepcopy(result), "tool": tool_name})
        self.tool_observations[observation.tool] = observation
        compatibility_key = TOOL_RESULT_COMPAT_KEYS[observation.tool]
        if not observation.success:
            self.collected_info.pop(compatibility_key, None)
            return
        self.collected_info[compatibility_key] = deepcopy(observation.data)
        if not isinstance(observation.data, dict):
            return
        for key in TOOL_IDENTIFIER_PROJECTIONS.get(observation.tool, ()):
            value = observation.data.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            if key == "order_id":
                if self.current_business_parameters().order_id not in (None, "", value):
                    continue
                try:
                    self.set_business_parameters(order_id=value)
                except ValueError:
                    continue
            else:
                self.collected_info[key] = value

    def invalidate_evidence(self, reason: str) -> None:
        epoch = self.evidence_bundle.knowledge_epoch if self.evidence_bundle else None
        self.evidence_bundle = EvidenceBundle(knowledge_epoch=epoch, items=[], support="unsupported")
        self.retrieval_error = reason
        self.review_result = None
        self.collected_info.update(kb_results=[], rag_fallback_reason=reason)
        self.set_flow(rag_grounded=False, rag_refused=None, answer_validated=None, answer_from_cache=None)
        for key in ("best_match_score", "validation", "answer_validated", "answer_from_cache", "llm_judge"):
            self.collected_info.pop(key, None)
        for call in self.tool_calls:
            if call.tool_name == "hybrid_search":
                call.success = False
                call.result = {"success": False, "results": [], "error": reason,
                               "evidence_bundle": self.evidence_bundle.model_dump()}

    def accept_retrieval(self, result: Dict[str, Any]) -> bool:
        """单一入口核实检索契约并独立复制，失败或双投影冲突都不能作为证据。"""
        metadata = result.get("meta") or {}
        if not isinstance(metadata, dict):
            self.retrieval_metadata = {}
            self.invalidate_evidence("retrieval_contract_invalid")
            return False
        self.retrieval_metadata = deepcopy(metadata)
        self.collected_info["kb_search_meta"] = deepcopy(self.retrieval_metadata)
        if result.get("success") is not True:
            self.invalidate_evidence("retrieval_failed")
            return False
        try:
            bundle = EvidenceBundle.model_validate(result.get("evidence_bundle") or {
                "items": result.get("results", []),
                "knowledge_epoch": self.retrieval_metadata.get("knowledge_epoch"),
            })
            if bundle.items != result.get("results", []):
                raise ValueError("evidence_projection_mismatch")
            epoch = self.retrieval_metadata.get("knowledge_epoch")
            if epoch is not None and epoch != bundle.knowledge_epoch:
                raise ValueError("evidence_epoch_mismatch")
        except (ValueError, TypeError):
            self.invalidate_evidence("retrieval_contract_invalid")
            return False
        bundle.support = "unknown"
        self.evidence_bundle = bundle.model_copy(deep=True)
        self.retrieval_error = None
        self.review_result = None
        self.collected_info["kb_results"] = deepcopy(bundle.items)
        self.set_flow(rag_grounded=None, rag_refused=None, answer_validated=None, answer_from_cache=None)
        self.collected_info.pop("best_match_score", None)
        for key in ("validation", "answer_validated", "answer_from_cache", "llm_judge"):
            self.collected_info.pop(key, None)
        return True

    @staticmethod
    def carryover_context(info: Dict[str, Any], *, unfinished: bool) -> Dict[str, Any]:
        """内存新轮次和SQL恢复共用白名单，持久存储不是本轮证据/授权来源。"""
        persistent_keys = {"order_id", "tracking_number", "ticket_id", "intent_history"}
        if unfinished:
            persistent_keys.update(
                {"reason", "refund_type", "refund_preview", "refund_action"}
            )
        return {key: deepcopy(value) for key, value in info.items() if key in persistent_keys}

    def for_next_turn(self, user_id: int, message: str) -> "AgentState":
        """Copy conversation facts and unfinished actions; reset every turn artifact."""
        # 兼容字段导入会建立类型状态，必须在副本中进行，不能修改上轮缓存/调用方对象。
        context_source = self.model_copy(deep=True)
        context = context_source.business_context()
        pending = list(self.pending_confirmations)
        if context_source.refund_preview_error or context_source.refund_action_error:
            pending = [value for value in pending if value != "apply_refund"]
        state = AgentState(
            session_id=self.session_id,
            user_id=user_id,
            user_message=message,
            messages=deepcopy(self.messages),
            conversation_summary=self.conversation_summary,
            detected_intent=self.detected_intent,
            intent_confidence=self.intent_confidence,
            needs_clarification=self.needs_clarification,
            pending_confirmations=pending,
            collected_info=self.carryover_context(
                context, unfinished=bool(self.missing_slots or pending)
            ),
        )
        state.collected_info.update({"user_id": user_id, "session_id": self.session_id})
        state.messages.append(
            {
                "role": "user",
                "content": message,
                "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            }
        )
        return state

    def add_trace(
        self,
        node_name: str,
        input_data: Any = None,
        output_data: Any = None,
        duration_ms: int = 0,
    ):
        """添加执行轨迹"""
        self.trace.append(
            {
                "node": node_name,
                "input": input_data,
                "output": output_data,
                "duration_ms": duration_ms,
                "timestamp": __import__("datetime").datetime.now().isoformat(),
            }
        )

    def add_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        result: Any = None,
        success: bool = True,
        error_message: Optional[str] = None,
        error_type: Optional[str] = None,
    ):
        """添加工具调用记录"""
        self.tool_calls.append(
            ToolCall(
                tool_name=tool_name,
                arguments=deepcopy(arguments),
                result=deepcopy(result),
                success=success,
                error_message=error_message,
                error_type=error_type,
            )
        )

    def get_last_tool_result(self, tool_name: str) -> Optional[Any]:
        """获取最后一个指定工具的调用结果"""
        for tool_call in reversed(self.tool_calls):
            if tool_call.tool_name == tool_name and tool_call.success:
                return tool_call.result
        return None


# 意图类型定义
class IntentType:
    QUERY_ORDER = "query_order"
    REFUND = "refund"
    COMPLAINT = "complaint"
    TECHNICAL = "technical"
    PROMOTION = "promotion"
    HUMAN = "human"
    GENERAL = "general"  # 兜底意图：无明确领域时由 general 承接


# Agent 节点名称
class AgentNode:
    START = "start"
    INTENT_RECOGNITION = "intent_recognition"
    ROUTE_DECISION = "route_decision"
    SLOT_FILLING = "slot_filling"
    QUERY_REWRITE = "query_rewrite"
    CLARIFICATION = "clarification"
    RAG_RETRIEVAL = "rag_retrieval"
    TOOL_EXECUTION = "tool_execution"
    CONFIRMATION = "confirmation"
    RESULT_VERIFICATION = "result_verification"
    RESPONSE_GENERATION = "response_generation"
    HUMAN_HANDOFF = "human_handoff"
    END = "end"
