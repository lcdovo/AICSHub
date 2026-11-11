"""任务对话记录；只记录原始用户反馈与通过审查的建议，不提取授权或订单事实。"""
from app.utils.utc_time import utc_now
from datetime import datetime, timezone
import re
from typing import TYPE_CHECKING

from app.agent.task_contracts import TaskNote
from app.agent.request_context import request_scope_digest

if TYPE_CHECKING:
    from app.agent.state import AgentState


def remember_task_turn(state: "AgentState", source: str, content: str) -> None:
    task = state.task
    if (not task or task.user_id != state.user_id or task.session_id != state.session_id
            or task.actor_id != (state.actor_id or state.user_id) or not content.strip()):
        return
    scope = request_scope_digest(state)
    if any(note.source == source and note.turn_digest == scope for note in task.notes):
        return
    note = TaskNote(source=source, message_id=state.source_message_id, turn_digest=scope,
                    content=content[:600], recorded_at=utc_now().isoformat())
    state.task = task.model_copy(update={"notes": [*task.notes, note][-24:]})


def task_context_payload(state: "AgentState") -> dict | None:
    task = state.task
    if (not task or task.user_id != state.user_id or task.session_id != state.session_id
            or task.actor_id != (state.actor_id or state.user_id)):
        return None
    remaining, selected = 1800, []
    for note in reversed(task.notes[-12:]):
        if note.turn_digest == request_scope_digest(state):
            continue
        if len(note.content) > remaining:
            break  # Keep complete source excerpts; do not silently change their meaning.
        selected.append(note.model_dump())
        remaining -= len(note.content)
    return {"goal": task.goal, "kind": task.kind, "order_id": task.order_id, "product": task.product,
            **({"items": [item.model_dump() for item in task.items]} if task.items else {}),
            "conditions": list(task.conditions), "waiting_for": task.waiting_for,
            "records": list(reversed(selected)), "trusted_as_current_evidence": False,
            "grants_authorization": False,
            "rule": "记录只说明历史用户陈述或已建议步骤；不得假设建议已执行、结果正常或故障已排除。"}


def repeated_diagnostic_issues(state: "AgentState", response: str) -> list[str]:
    """有限的精确重复保护；不能声称覆盖语义改写后的所有重复步骤。"""
    if (not state.task or state.task.kind != "troubleshoot" or not state.task_runtime
            or state.task_runtime.response_goal != "diagnostic_feedback"
            or not re.search(r"仍|还是|不行|无效|没用|试过|检查过|已经", state.user_message)
            or re.search(r"再说一遍|重复.*步骤|重新说明|再发|还没试|还没操作", state.user_message)):
        return []
    def normalized(text: str) -> str:
        body = re.split(r"\n+\s*参考来源[：:]", text, maxsplit=1)[0]
        return re.sub(r"[\s，,。；;！？!?]", "", body)
    candidate = normalized(response)
    return ["repeated_diagnostic_step"] if any(note.source == "suggested_step"
        and note.turn_digest != request_scope_digest(state) and normalized(note.content) == candidate
        for note in state.task.notes) else []
