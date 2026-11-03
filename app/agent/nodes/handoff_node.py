"""人工转接节点：调用 escalate_to_human 工具并输出转接话术。"""
import logging

from app.agent.state import AgentState, AgentNode
from app.agent.mcp_client import invoke_tool
from app.agent.tools import ToolExecutionResult
from app.agent.handoff_authorization import authorize_handoff
from app.services.ticket_creation import handoff_message

logger = logging.getLogger(__name__)


async def human_handoff_node(self: "AgentGraph", state: AgentState) -> str:
    state.collected_info["handoff_attempted"] = True
    arguments = {"reason": (state.human_reason or "用户要求转人工")[:100],
                 "priority": "urgent" if state.need_human else "normal", "context": "",
                 "user_id": state.user_id, "session_id": state.session_id,
                 "idempotency_key": f"chat-handoff:{state.session_id}:{state.source_message_id}"}
    try:
        authorization = authorize_handoff(arguments, state.source_message_id, state.actor_id)
        handoff = await invoke_tool("escalate_to_human", **arguments, authorization=authorization)
        if handoff is None:
            handoff = ToolExecutionResult(success=False, error="人工请求工具不可用", error_type="not_found")
    except Exception:
        logger.warning("Handoff execution context or service unavailable")
        handoff = ToolExecutionResult(success=False, error="人工请求执行上下文无效或服务不可用", error_type="execution")
    data = handoff.data if isinstance(handoff.data, dict) else {}
    if handoff.success and (not isinstance(data.get("request_id"), str) or not data["request_id"].strip()
                            or not isinstance(data.get("status"), str)
                            or data.get("status") not in {"pending", "assigned", "resolved", "cancelled"}
                            or type(data.get("user_id")) is not int or data["user_id"] != state.user_id
                            or data.get("session_id") != state.session_id):
        handoff = ToolExecutionResult(success=False, error="人工请求缺少可信回执", error_type="unknown")
    state.add_tool_call(tool_name="escalate_to_human", arguments=arguments, result=handoff.model_dump(),
                        success=handoff.success, error_message=handoff.error, error_type=handoff.error_type)
    state.reply = handoff_message(data) if handoff.success else "暂时无法确认转人工请求结果，请查看请求记录或联系支持。"

    return AgentNode.RESPONSE_GENERATION
