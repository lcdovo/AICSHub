"""工具执行链路节点：业务工具执行、退款人工确认、结果校验与信息抽取。"""
import logging
import re
from typing import Any, Dict, List

from langgraph.types import interrupt

from app.agent.state import AgentState, AgentNode, IntentType
from app.agent.mcp_client import invoke_tool

logger = logging.getLogger(__name__)


async def tool_execution_node(self: "AgentGraph", state: AgentState) -> str:
    state.collected_info["user_id"] = state.user_id
    state.collected_info["session_id"] = state.session_id
    extracted_info = self._extract_info_from_message(state.user_message)

    for key, value in extracted_info.items():
        if key == "order_id" or key not in state.collected_info or not state.collected_info[key]:
            state.collected_info[key] = value

    tools_to_execute = self._select_tools_for_intent(
        state.detected_intent, state.collected_info
    )

    # 退款是不可逆副作用：第一次请求只生成预览，必须在独立确认节点中得到
    # 明确同意后才能调用工具。确认状态放在 AgentState 中，跨普通/流式入口一致。
    if state.detected_intent == IntentType.REFUND and tools_to_execute:
        if not self._refund_confirmed(state):
            state.pending_confirmations = ["apply_refund"]
            state.collected_info["refund_preview"] = tools_to_execute[0][1]
            return AgentNode.CONFIRMATION
        preview = state.collected_info.get("refund_preview")
        if preview:
            tools_to_execute = [("apply_refund", dict(preview))]
        state.pending_confirmations = []

    for tool_name, tool_args in tools_to_execute:
        try:
            result = await invoke_tool(tool_name, **tool_args)
            if result is None:
                # 工具不存在：跳过，不记录失败（与原跳过行为一致）。
                continue

            state.add_tool_call(
                tool_name=tool_name,
                arguments=tool_args,
                result={
                    "success": result.success,
                    "data": result.data,
                    "error": result.error,
                    "hint": result.hint,
                    "execution_time_ms": result.execution_time_ms,
                    "retry_count": result.retry_count,
                    "error_type": result.error_type,
                },
                success=result.success,
                error_message=result.error,
                error_type=result.error_type,
            )

            if result.success and result.data:
                if isinstance(result.data, dict):
                    state.collected_info.update(result.data)
                state.collected_info[f"{tool_name}_result"] = result.data
                if tool_name == "apply_refund":
                    state.collected_info.pop("refund_preview", None)
                    state.collected_info.pop("confirm_refund", None)

            if not result.success:
                if result.error and "人工" in result.error:
                    state.need_human = True
                    state.human_reason = result.error
                    return AgentNode.HUMAN_HANDOFF

                if result.retry_count >= 3:
                    state.need_human = True
                    state.human_reason = f"工具 {tool_name} 连续 {result.retry_count} 次失败"
                    logger.warning(f"工具连续失败，转人工: {state.human_reason}")
                    return AgentNode.HUMAN_HANDOFF

        except Exception as e:
            logger.error(f"工具 {tool_name} 执行异常: {e}")
            state.add_tool_call(
                tool_name=tool_name,
                arguments=tool_args,
                result=None,
                success=False,
                error_message=str(e),
            )

    return AgentNode.RESULT_VERIFICATION


def refund_confirmed(self: "AgentGraph", state: AgentState) -> bool:
    """识别显式确认，避免将“好的/谢谢”等普通回复当作授权。"""
    if state.collected_info.get("confirm_refund") is True:
        return True
    confirmations = {"确认", "同意", "确定", "提交", "是的", "可以"}
    message = state.user_message.strip()
    return message in confirmations



def refund_cancelled(state: AgentState) -> bool:
    return state.user_message.strip() in {"取消", "不同意", "先不退", "不提交", "算了", "false", "0"}


async def confirmation_node(self: "AgentGraph", state: AgentState) -> str:
    preview = state.collected_info.get("refund_preview", {})
    if self._refund_confirmed(state):
        state.collected_info["confirm_refund"] = True
        state.pending_confirmations = []
        return AgentNode.TOOL_EXECUTION

    order_id = preview.get("order_id", "")
    refund_type = preview.get("type", "refund")
    reason = preview.get("reason", "")[:80]
    label = "退货退款" if refund_type in {"return", "exchange"} else "仅退款"
    state.reply = (
        f"即将为订单 {order_id} 提交{label}申请，原因：{reason or '未填写'}。"
        "该操作提交后将进入审核，是否确认提交？（请回复“确认”）"
    )
    decision = interrupt({
        "type": "refund_confirmation", "order_id": order_id,
        "refund_type": refund_type, "reason": reason,
    })
    if decision is True or str(decision).strip() in {"确认", "同意", "确定", "提交", "是的", "可以", "true", "1"}:
        state.collected_info["confirm_refund"] = True
        state.pending_confirmations = []
        return AgentNode.TOOL_EXECUTION
    return AgentNode.RESPONSE_GENERATION



def extract_info_from_message(self: "AgentGraph", message: str) -> Dict[str, Any]:
    info = {}

    order_patterns = [
        r"ORD\d{6,}",
        r"订单[：:]\s*(\w+)",
        r"订单号[：:]\s*(\w+)",
    ]
    for pattern in order_patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            order_id = match.group(0) if len(match.groups()) == 0 else match.group(1)
            info["order_id"] = order_id
            break

    if any(word in message for word in ["投诉", "垃圾", "骗子", "气愤"]):
        info["complaint_content"] = message

    if any(word in message for word in ["退款", "退货", "退换"]):
        info["reason"] = message
        if "换" in message:
            info["refund_type"] = "exchange"
        else:
            info["refund_type"] = "refund"

    return info


def select_tools_for_intent(
    self: "AgentGraph", intent: str, collected_info: Dict[str, Any]
) -> List[tuple]:
    tool_plan = []

    if intent == IntentType.QUERY_ORDER:
        order_id = collected_info.get("order_id")
        if order_id:
            tool_plan.append(("query_order", {"order_id": order_id, "user_id": collected_info.get("user_id", 0)}))

    elif intent == IntentType.REFUND:
        order_id = collected_info.get("order_id", "")
        reason = collected_info.get("reason", "")
        refund_type = collected_info.get("refund_type", "refund")

        if order_id:
            tool_plan.append((
                "apply_refund",
                {
                    "order_id": order_id,
                    "reason": reason,
                    "type": refund_type,
                    "user_id": collected_info.get("user_id", 0),
                    "idempotency_key": f"refund:{collected_info.get('session_id', '')}:{order_id}:{refund_type}",
                },
            ))

    elif intent == IntentType.COMPLAINT:
        complaint_content = collected_info.get("complaint_content", "")
        tool_plan.append((
            "create_ticket",
            {
                "user_id": collected_info.get("user_id", 1),
                "category": "投诉建议",
                "content": complaint_content or "用户投诉",
                "priority": "high",
            },
        ))

    return tool_plan


async def result_verification_node(self: "AgentGraph", state: AgentState) -> str:
    if state.tool_calls:
        last_call = state.tool_calls[-1]

        if not last_call.success:
            tool_count = len(state.tool_calls)
            if tool_count < 3:
                state.reply = f"抱歉，操作遇到问题：{last_call.error_message or '未知错误'}"
            else:
                state.need_human = True
                state.human_reason = "工具调用连续失败"
                logger.warning(f"工具连续失败 ({tool_count}次)，转人工")
                return AgentNode.HUMAN_HANDOFF

        if last_call.success and isinstance(last_call.result, dict):
            execution_time = last_call.result.get("execution_time_ms", 0)
            retry_count = last_call.result.get("retry_count", 0)
            if execution_time > 3000:
                logger.warning(
                    f"工具执行耗时过长: {execution_time}ms"
                )

            if retry_count > 0:
                logger.info(
                    f"工具执行重试 {retry_count} 次后成功"
                )

    return AgentNode.RESPONSE_GENERATION