"""人工转接节点：调用 escalate_to_human 工具并输出转接话术。"""
import logging
from dataclasses import dataclass
from typing import Callable

from app.agent.state import AgentState, AgentNode
from app.agent.mcp_client import invoke_tool
from app.agent.tools import ToolExecutionResult
from app.agent.handoff_authorization import authorize_handoff
from app.services.ticket_creation import handoff_message
from app.agent.nodes.tool_node import ToolInvoker
from app.agent.handoff_evidence import matching_handoff_receipt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HandoffDependencies:
    invoke: ToolInvoker
    authorize: Callable[[dict, int | None, int | None], str]


@dataclass(frozen=True)
class HandoffNode:
    dependencies: HandoffDependencies

    async def __call__(self, state: AgentState) -> str:
        return await request_handoff(self.dependencies, state)


async def invoke_handoff_tool(name: str, **arguments):
    return await invoke_tool(name, **arguments)


def authorize_current_handoff(arguments, message_id, actor_id):
    return authorize_handoff(arguments, message_id, actor_id)


async def request_handoff(dependencies: HandoffDependencies, state: AgentState) -> str:
    state.set_flow(handoff_attempted=True)
    arguments = {"reason": (state.human_reason or "用户要求转人工")[:100],
                 "priority": "urgent" if state.need_human else "normal", "context": "",
                 "user_id": state.user_id, "session_id": state.session_id,
                 "idempotency_key": f"chat-handoff:{state.session_id}:{state.source_message_id}"}
    try:
        authorization = dependencies.authorize(arguments, state.source_message_id, state.actor_id)
        handoff = await dependencies.invoke("escalate_to_human", **arguments, authorization=authorization)
        if handoff is None:
            handoff = ToolExecutionResult(success=False, error="人工请求工具不可用", error_type="not_found")
    except Exception:
        logger.warning("Handoff execution context or service unavailable")
        handoff = ToolExecutionResult(success=False, error="人工请求执行上下文无效或服务不可用", error_type="execution")
    data = handoff.data if isinstance(handoff.data, dict) else {}
    if handoff.success and not matching_handoff_receipt(data, state.user_id, state.session_id):
        handoff = ToolExecutionResult(success=False, error="人工请求缺少可信回执", error_type="unknown")
    state.record_tool_observation("escalate_to_human", handoff.model_dump())
    state.add_tool_call(tool_name="escalate_to_human", arguments=arguments, result=handoff.model_dump(),
                        success=handoff.success, error_message=handoff.error, error_type=handoff.error_type)
    state.reply = handoff_message(data) if handoff.success else "暂时无法确认转人工请求结果，请查看请求记录或联系支持。"

    return AgentNode.RESPONSE_GENERATION


async def human_handoff_node(graph, state: AgentState) -> str:
    return await request_handoff(HandoffDependencies(invoke_handoff_tool, authorize_current_handoff), state)
