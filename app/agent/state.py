"""
Agent 状态定义 - 定义 Agent 在执行过程中需要追踪的所有状态
"""

from typing import Any, List, Dict, Optional, Literal
from pydantic import BaseModel, Field
from copy import deepcopy
from datetime import datetime
from app.agent.read_plan import ReadTask


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
    understanding_mode: Optional[str] = None
    strategy_version: Optional[str] = None
    node_steps: int = 0
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
                arguments=arguments,
                result=result,
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
