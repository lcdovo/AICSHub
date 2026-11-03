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
            if key == "order_id" and self.collected_info.get(key) not in (None, "", value):
                continue
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

    def for_next_turn(self, user_id: int, message: str) -> "AgentState":
        """Copy conversation facts and unfinished actions; reset every turn artifact."""
        persistent_keys = {"order_id", "tracking_number", "ticket_id", "intent_history"}
        if self.missing_slots or self.pending_confirmations:
            persistent_keys.update(
                {"reason", "refund_type", "refund_preview", "refund_action"}
            )
        state = AgentState(
            session_id=self.session_id,
            user_id=user_id,
            user_message=message,
            messages=deepcopy(self.messages),
            conversation_summary=self.conversation_summary,
            detected_intent=self.detected_intent,
            intent_confidence=self.intent_confidence,
            needs_clarification=self.needs_clarification,
            pending_confirmations=list(self.pending_confirmations),
            collected_info={
                key: deepcopy(value)
                for key, value in self.collected_info.items()
                if key in persistent_keys
            },
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
