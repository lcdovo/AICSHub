"""本轮工具观察契约；工具数据不能声明调用者身份、确认或回答校验状态。"""

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict

BusinessToolName = Literal[
    "query_order", "create_ticket", "apply_refund", "search_kb",
    "escalate_to_human", "send_notification", "update_ticket_status", "get_user_history",
]


class ToolObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool: BusinessToolName
    success: bool
    data: Any = None
    error: str | None = None
    hint: str | None = None
    execution_time_ms: int = 0
    retry_count: int = 0
    error_type: str | None = None
    call_id: str | None = None
    observed_at: str | None = None
    transport: Literal["mcp", "direct", "receipt"] | None = None
    outcome: Literal["succeeded", "rejected", "failed", "unknown"] | None = None


TOOL_RESULT_COMPAT_KEYS: dict[str, str] = {
    "query_order": "query_order_result", "create_ticket": "create_ticket_result",
    "apply_refund": "apply_refund_result", "search_kb": "search_kb_result",
    "escalate_to_human": "escalate_to_human_result", "send_notification": "send_notification_result",
    "update_ticket_status": "update_ticket_status_result", "get_user_history": "get_user_history_result",
}

# 只投影业务编号供已有流程展示/补全；全部回执保留在观察与ToolCall，不展开任意字段。
TOOL_IDENTIFIER_PROJECTIONS: dict[str, tuple[str, ...]] = {
    "query_order": ("order_id",), "create_ticket": ("ticket_id",),
    "apply_refund": ("refund_id",), "update_ticket_status": ("ticket_id",),
    "escalate_to_human": ("request_id",), "send_notification": ("notification_id",),
}
