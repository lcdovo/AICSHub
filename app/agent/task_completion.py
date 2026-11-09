"""服务端任务完成与实质进展检查，不接受模型自述成功。"""
import hashlib
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.state import AgentState


def required_capabilities(state: "AgentState") -> list[str]:
    value = state.understanding
    required = {read.tool for read in value.read_tasks}
    requests = [(value.topic, value.request_type), *[(r.topic, r.request_type) for r in value.subrequests]]
    if value.conditions or any(topic == "query_order" and kind == "query" for topic, kind in requests):
        required.add("query_order")
    if state.task.kind == "troubleshoot":
        required.update(["knowledge", "diagnostic_resolution"])
    if any(kind == "inquiry" and topic != "query_order" for topic, kind in requests):
        required.add("knowledge")
    if state.task.refund_requested:
        required.update(["query_order", "knowledge", "refund_policy", "refund_preview"])
    return sorted(required)


def completion_gaps(state: "AgentState") -> list[str]:
    from app.agent.intent_items import active, execution_gaps
    if active(state):
        return execution_gaps(state)
    from app.agent.task_loop import current_order
    required = state.task.requirements or required_capabilities(state)
    gaps = []
    for capability in required:
        if capability == "query_order":
            met = bool(current_order(state))
        elif capability == "get_user_history":
            observation = state.tool_observations.get(capability)
            met = bool(observation and observation.success)
        elif capability == "knowledge":
            met = bool(state.current_evidence())
        elif capability == "refund_policy":
            met = bool(state.current_evidence()) and any(s.capability == "search_knowledge:refund_policy"
                and s.status == "succeeded" for s in state.task_runtime.steps)
        elif capability == "refund_preview":
            met = bool(state.current_refund_preview())
        else:
            message = state.user_message
            met = bool(re.search(r"问题(?:已经|已)?解决了|故障(?:已经|已)?解决|现在能用了|现在可以正常使用", message)) and not re.search(
                r"没有|并未|还没|是否|如果|吗|以前|之前|曾经|据说|客服说|[“”\"？?]", message)
        if not met:
            gaps.append(capability)
    return gaps


def progress_signature(state: "AgentState") -> str:
    values = {"tools": {name: observation.data for name, observation in state.tool_observations.items()
                        if observation.success and name in {"query_order", "get_user_history"}},
              "knowledge": sorted({(str(item.get("version_id")), " ".join(str(item.get("content", "")).split()))
                                   for item in state.current_evidence()})}
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
