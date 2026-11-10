"""工具执行链路节点：业务工具执行、退款人工确认、结果校验与信息抽取。"""
import logging
import re
from typing import Any, Dict, List, Callable, Protocol
from dataclasses import dataclass

from langgraph.types import interrupt

from app.agent.state import AgentState, AgentNode, IntentType
from app.agent.tools import ToolExecutionResult
from app.agent.mcp_client import invoke_tool
from app.agent.ticket_authorization import authorize_ticket
from app.agent.refund_authorization import (authorize_refund, CONFIRMATIONS,
    is_refund_confirmation_message, is_refund_cancellation_message)

logger = logging.getLogger(__name__)


class RefundActions(Protocol):
    async def prepare(self, actor_id: int, payload: dict, previous: str | None) -> dict: ...
    async def pending_count(self, user_id: int, session_id: str) -> int: ...


class ToolInvoker(Protocol):
    async def __call__(self, name: str, **arguments: Any) -> ToolExecutionResult | None: ...


@dataclass(frozen=True)
class ToolExecutionDependencies:
    extract_info: Callable[[str], dict]
    select_tools: Callable[[str, dict], list[tuple]]
    confirmed: Callable[[AgentState], bool]
    invoke: ToolInvoker
    actions: RefundActions


@dataclass(frozen=True)
class ConfirmationDependencies:
    confirmed: Callable[[AgentState], bool]
    pause: Callable[[dict], Any]


@dataclass(frozen=True)
class ToolExecutionNode:
    dependencies: ToolExecutionDependencies

    async def __call__(self, state: AgentState) -> str:
        return await execute_tools(self.dependencies, state)


@dataclass(frozen=True)
class ConfirmationNode:
    dependencies: ConfirmationDependencies

    async def __call__(self, state: AgentState) -> str:
        return await confirm_action(self.dependencies, state)


async def invoke_registered_tool(name: str, **arguments) -> ToolExecutionResult | None:
    return await invoke_tool(name, **arguments)


def persistent_actions():
    from app.services.refund_actions import RefundActionCoordinator
    from app.utils import database
    return RefundActionCoordinator(lambda: database.async_session)


async def execute_tools(dependencies: "ToolExecutionDependencies", state: AgentState) -> str:
    if state.read_plan:
        from app.agent.read_plan import ReadPrincipal, run_authorized_reads, summarize_reads
        # 即使内部调用绕过理解节点，也必须重新验证计划，模型不能提供Principal。
        from app.agent.request_understanding import validate_semantics
        try:
            if not state.understanding or state.read_plan != state.understanding.read_tasks:
                raise ValueError("read_plan_without_understanding")
            validate_semantics(state.understanding, state)
            results = await run_authorized_reads(state.read_plan, ReadPrincipal(
                actor_id=state.actor_id or state.user_id, user_id=state.user_id,
                session_id=state.session_id,
            ))
            for task, (payload, result) in zip(state.read_plan, results):
                if result is None:
                    result = ToolExecutionResult(success=False, error="只读服务不可用", error_type="unavailable")
                state.record_tool_observation(task.tool, result.model_dump())
                state.add_tool_call(task.tool, payload, result.model_dump(), result.success,
                                    result.error, result.error_type)
            state.reply = summarize_reads(
                state.read_plan,
                results,
                has_conditions=bool(state.understanding.conditions),
                user_message=state.user_message,
            )
            if all(task.tool == "query_order" for task in state.read_plan) and all(
                result is not None and result.success and isinstance(result.data, dict)
                and result.data.get("order_id") == task.resource_id
                for task, (_, result) in zip(state.read_plan, results)
            ):
                # 仅服务端固定订单回显可作通知；历史自由文本仍需独立语义审查。
                state.set_workflow_reply(state.reply)
        except (ValueError, TypeError, TimeoutError):
            state.set_workflow_reply("本轮只读查询未完成，请核对查询对象后重试；后续业务操作尚未执行。")
            state.needs_clarification = True
            return AgentNode.RESPONSE_GENERATION
        return AgentNode.RESULT_VERIFICATION
    state.current_refund_preview()
    state.current_refund_action()
    if state.refund_preview_error or state.refund_action_error:
        state.set_workflow_reply("退款预览无效或已变化，请重新核对并生成预览。")
        state.needs_clarification = True
        return AgentNode.RESPONSE_GENERATION
    state.collected_info["user_id"] = state.user_id
    state.collected_info["session_id"] = state.session_id
    if state.understanding is None:
        extracted_info = dependencies.extract_info(state.user_message)
        if not state.merge_extracted_business_parameters(extracted_info, model_selected_order=False):
            state.set_workflow_reply("本轮业务参数无效，请核对订单号、原因和申请类型。")
            state.needs_clarification = True
            return AgentNode.RESPONSE_GENERATION
    else:
        from app.agent.request_understanding import validate_semantics, execution_request_type, execution_missing_information
        from app.agent.routing_policy import DEFAULT_ROUTING_POLICY
        try:
            value = state.understanding
            validate_semantics(value, state)
            expected_topic = "query_order" if value.conditions else value.topic
            if state.detected_intent != expected_topic:
                raise ValueError("understood_topic_changed")
            if state.current_business_parameters().order_id != value.entities.order_id:
                raise ValueError("understood_order_changed")
            for key, selected in value.parameters.model_dump(exclude_none=True).items():
                if getattr(state.current_business_parameters(), key) != selected:
                    raise ValueError("understood_parameter_changed")
            decision = DEFAULT_ROUTING_POLICY.decide_structured(
                topic=expected_topic, request_type=execution_request_type(value),
                parameters=state.current_business_parameters().model_dump(exclude_none=True),
                missing_information=execution_missing_information(value))
        except ValueError:
            state.set_workflow_reply("本轮业务对象或理解已变化，请重新核实；未执行后续操作。")
            state.needs_clarification = True
            return AgentNode.RESPONSE_GENERATION
        if decision.next_node != AgentNode.TOOL_EXECUTION:
            state.missing_slots = list(decision.missing_slots)
            if decision.reply:
                state.reply = decision.reply
            state.needs_clarification = True
            return decision.next_node

    tools_to_execute = dependencies.select_tools(
        state.detected_intent, {**state.current_business_parameters().model_dump(exclude_none=True),
                                "user_id": state.user_id, "session_id": state.session_id}
    )

    # 退款是不可逆副作用：第一次请求只生成预览，必须在独立确认节点中得到
    # 明确同意后才能调用工具。确认状态放在 AgentState 中，跨普通/流式入口一致。
    if state.detected_intent == IntentType.REFUND and tools_to_execute:
        if not dependencies.confirmed(state):
            if re.search(r"部分退款|只退|(?:退款|退回|退还)\s*\d+(?:\.\d+)?\s*(?:元|块|CNY)", state.user_message, re.I):
                state.set_workflow_reply("本版仅支持模拟全额申请，不支持指定金额或部分退款；请确认是否申请订单全额退款。")
                state.needs_clarification = True
                return AgentNode.RESPONSE_GENERATION
            if state.source_message_id is not None:
                from fastapi import HTTPException
                try:
                    previous = state.refund_preview_data().get("action_id")
                    action = await dependencies.actions.prepare(state.actor_id or state.user_id,
                                           tools_to_execute[0][1] | {"session_id": state.session_id}, previous)
                    if not isinstance(action, dict) or not state.record_refund_action(action):
                        if not isinstance(action, dict):
                            state.record_refund_action({})
                        state.set_workflow_reply("无法核实退款动作回执，请查询当前记录或重新生成预览；未提交退款申请。")
                        state.needs_clarification = True
                        return AgentNode.RESPONSE_GENERATION
                    tools_to_execute[0][1].update(action_id=action["action_id"], action_version=action["version"],
                                                  idempotency_key=f"refund-action:{action['action_id']}")
                except HTTPException as exc:
                    state.reply, state.needs_clarification = str(exc.detail), True
                    return AgentNode.RESPONSE_GENERATION
            state.pending_confirmations = ["apply_refund"]
            state.set_refund_preview({**tools_to_execute[0][1], "session_id": state.session_id})
            return AgentNode.CONFIRMATION
        preview = state.refund_preview_data()
        if state.source_message_id is not None:
            pending_count = await dependencies.actions.pending_count(state.user_id, state.session_id)
            if pending_count > 1 and state.requested_action_id is None:
                state.set_workflow_reply("有多个待确认退款动作，请指定action_id与版本。")
                state.needs_clarification = True
                return AgentNode.RESPONSE_GENERATION
            if state.requested_action_id and preview:
                preview = {**preview, "action_id": state.requested_action_id,
                           "action_version": state.requested_action_version}
        if preview:
            tools_to_execute = [("apply_refund", dict(preview))]
        state.pending_confirmations = []

    for tool_name, tool_args in tools_to_execute:
        logged_args = dict(tool_args)
        try:
            if tool_name in {"query_order", "get_user_history"}:
                from app.agent.read_authorization import authorize_read, HistoryReadInput
                tool_args["user_id"] = state.user_id
                if tool_name == "get_user_history":
                    tool_args = HistoryReadInput.model_validate(tool_args).model_dump()
                logged_args = dict(tool_args)
                tool_args["authorization"] = authorize_read(tool_name, tool_args, state.actor_id or state.user_id)
            elif tool_name == "create_ticket":
                tool_args.update(user_id=state.user_id, session_id=state.session_id,
                                 idempotency_key=f"chat-ticket:{state.session_id}:{state.source_message_id}")
                logged_args = dict(tool_args)
                tool_args["authorization"] = authorize_ticket(tool_args, state.source_message_id, state.actor_id)
            elif tool_name == "apply_refund":
                tool_args.update(user_id=state.user_id, session_id=state.session_id)
                logged_args = dict(tool_args)
                # 无持久来源的离线/内部调用仍交由工具明确拒绝，不能从历史取得授权。
                tool_args["authorization"] = (authorize_refund(tool_args, state.source_message_id, state.actor_id)
                                              if state.source_message_id is not None else "")
            result = await dependencies.invoke(tool_name, **tool_args)
            if result is None:
                result = ToolExecutionResult(success=False, error="工具不存在或不可用", error_type="not_found")

            state.record_tool_observation(tool_name, result.model_dump())

            state.add_tool_call(
                tool_name=tool_name,
                arguments=logged_args,
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
                if tool_name == "apply_refund":
                    state.set_refund_preview(None)
                    state.record_refund_action(None)
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
            error = "动作执行上下文无效或服务不可用" if tool_name in {"create_ticket", "apply_refund"} else str(e)
            logger.error("工具 %s 执行异常: %s", tool_name, error)
            state.add_tool_call(
                tool_name=tool_name,
                arguments=logged_args,
                result=None,
                success=False,
                error_message=error,
            )

    return AgentNode.RESULT_VERIFICATION


def is_refund_confirmed(state: AgentState) -> bool:
    """识别显式确认，避免将“好的/谢谢”等普通回复当作授权。"""
    message = state.user_message.strip()
    preview = state.refund_preview_data() or {}
    return is_refund_confirmation_message(message, preview.get("type")) or state.has_current_refund_confirmation()



def refund_cancelled(state: AgentState) -> bool:
    return is_refund_cancellation_message(state.user_message)


async def confirm_action(dependencies: "ConfirmationDependencies", state: AgentState) -> str:
    preview = state.refund_preview_data()
    action = state.current_refund_action()
    if state.refund_preview_error or state.refund_action_error:
        state.set_workflow_reply("退款预览无效或已变化，请重新生成后确认。")
        state.needs_clarification = True
        return AgentNode.RESPONSE_GENERATION
    if action is not None and (not action.matches(state.refund_preview) or not action.is_pending()):
        state.set_workflow_reply("当前动作回执与预览不一致、已过期或已不是待确认状态，请查询记录或重新生成预览。")
        state.needs_clarification = True
        state.pending_confirmations = [value for value in state.pending_confirmations if value != "apply_refund"]
        return AgentNode.RESPONSE_GENERATION
    if dependencies.confirmed(state):
        if is_refund_confirmation_message(state.user_message, preview.get("type")):
            state.record_refund_confirmation("current_message")
        elif not state.has_current_refund_confirmation():
            state.set_workflow_reply("请在本轮明确确认当前退款预览。")
            state.needs_clarification = True
            return AgentNode.RESPONSE_GENERATION
        state.pending_confirmations = []
        return AgentNode.TOOL_EXECUTION

    order_id = preview.get("order_id", "")
    refund_type = preview.get("type", "refund")
    reason = preview.get("reason", "")[:80]
    label = {"return": "退货退款", "exchange": "换货"}.get(refund_type, "仅退款")
    detail = (f"模拟金额{action.amount} {action.currency}；动作{action.action_id}，版本{action.version}，"
              f"有效至{action.expires_at}。" if action is not None else "")
    if action is not None and action.preconditions:
        labels = {"order_not_shipped": "订单未发货", "order_delivered": "订单已签收"}
        detail += "办理前提：" + "、".join(labels[item] for item in action.preconditions) + "，提交时会再次核验。"
    state.set_workflow_reply(detail + (
        f"即将为订单 {order_id} 提交{label}申请，原因：{reason or '未填写'}。"
        "该操作提交后将进入审核，是否确认提交？（请回复“确认”）"
    ))
    decision = dependencies.pause({
        "type": "refund_confirmation", "order_id": order_id,
        "refund_type": refund_type, "reason": reason,
    })
    if decision is True or str(decision).strip() in CONFIRMATIONS | {"true", "1"}:
        state.record_refund_confirmation("interrupt")
        state.pending_confirmations = []
        return AgentNode.TOOL_EXECUTION
    return AgentNode.RESPONSE_GENERATION



def extract_info(message: str) -> Dict[str, Any]:
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


def select_tools(
    intent: str, collected_info: Dict[str, Any]
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


async def verify_tool_result(state: AgentState) -> str:
    if state.tool_calls:
        last_call = state.tool_calls[-1]

        if not last_call.success:
            if last_call.tool_name == "apply_refund":
                # Only fixed server text is trusted. Unknown outcomes must not be
                # described as a failed submission or retried by the model.
                known_rejection = last_call.error_type in {"conflict", "validation", "permission", "not_found"}
                state.set_workflow_reply(
                    "本次退款提交未通过条件、权限或预览一致性校验，未登记新申请。请重新核实订单和预览后再确认。"
                    if known_rejection else
                    "当前退款提交结果暂时无法核实，请使用原请求查询记录；不要重复提交。"
                )
                state.needs_clarification = True
                state.need_human = False
                return AgentNode.RESPONSE_GENERATION
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


# 私有方法兼容层，主图使用显式依赖节点与无图状态函数。
def refund_confirmed(graph, state: AgentState) -> bool:
    return is_refund_confirmed(state)


def extract_info_from_message(graph, message: str) -> Dict[str, Any]:
    return extract_info(message)


def select_tools_for_intent(graph, intent: str, collected_info: Dict[str, Any]) -> List[tuple]:
    return select_tools(intent, collected_info)


async def tool_execution_node(graph, state: AgentState) -> str:
    return await execute_tools(ToolExecutionDependencies(
        graph._extract_info_from_message, graph._select_tools_for_intent,
        graph._refund_confirmed, invoke_registered_tool, persistent_actions(),
    ), state)


async def confirmation_node(graph, state: AgentState) -> str:
    return await confirm_action(ConfirmationDependencies(graph._refund_confirmed, interrupt), state)


async def result_verification_node(graph, state: AgentState) -> str:
    return await verify_tool_result(state)
