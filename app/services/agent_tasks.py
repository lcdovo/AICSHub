"""与聊天最终提交共事务的任务CAS；恢复只取提示，绝不重跑旧工具或旧写。"""
from app.utils.utc_time import utc_now_db
from datetime import datetime, timedelta
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.task_contracts import TaskProgress
from app.models.models import AgentTask, AgentTaskStep, RefundAction


async def load_pending_task(db: AsyncSession, user_id: int, session_id: str) -> TaskProgress | None:
    row = await db.scalar(select(AgentTask).where(
        AgentTask.user_id == user_id, AgentTask.session_id == session_id,
        AgentTask.status.in_(["waiting_input", "waiting_confirmation"]),
        AgentTask.updated_at >= utc_now_db() - timedelta(hours=24),
    ).order_by(AgentTask.updated_at.desc()).limit(1))
    if row is None:
        return None
    try:
        task = TaskProgress.model_validate(row.progress)
    except (ValueError, TypeError):
        return None
    if (task.task_id != row.id or task.user_id != user_id or task.session_id != session_id
            or task.actor_id != row.actor_id or task.version != row.version or task.status != row.status):
        return None
    return task


async def persist_task(db: AsyncSession, state) -> None:
    """调用方持有原会话租约/fence；本函数不commit，不另开事务。"""
    from app.agent.memory import SessionLeaseError
    if state.superseded_task_id:
        await db.execute(update(AgentTask).where(AgentTask.id == state.superseded_task_id,
            AgentTask.user_id == state.user_id, AgentTask.session_id == state.session_id,
            AgentTask.status.in_(["waiting_input", "waiting_confirmation"])).values(
                status="stopped", updated_at=utc_now_db(), version=AgentTask.version + 1))
    if state.task is None:
        return
    task = state.task
    if (task.user_id != state.user_id or task.session_id != state.session_id
            or task.actor_id != (state.actor_id or state.user_id)):
        raise SessionLeaseError("任务主体发生变化")
    # 确认/取消仍经原领域服务，任务只跟随已取得的回执，不能驱动提交。
    if task.status == "waiting_confirmation":
        action = await db.get(RefundAction, task.action_id) if task.action_id else None
        if (action is not None and action.user_id == task.user_id and action.session_id == task.session_id
                and (action.status in {"submitted", "cancelled"} or action.expires_at <= utc_now_db())):
            status = "completed" if action.status == "submitted" else "cancelled" if action.status == "cancelled" else "stopped"
            task = task.model_copy(update={"status": status, "waiting_for": None,
                                          "stop_reason": "confirmation_" + action.status})
            if task.items:
                outcomes = dict(task.item_outcomes)
                for item in task.items:
                    if item.kind == "prepare_refund":
                        outcomes[item.item_id] = "submitted" if action.status == "submitted" else "cancelled" if action.status == "cancelled" else "limited"
                if status == "completed" and (set(outcomes) != {item.item_id for item in task.items}
                        or any(value in {"limited", "waiting_feedback", "pending"} for value in outcomes.values())):
                    status = "waiting_input"
                task = task.model_copy(update={"item_outcomes": outcomes, "status": status,
                    "waiting_for": "item_follow_up" if status == "waiting_input" else None})
    # 未被本轮承接的旧任务不重新更新时间或制造新步骤。
    if state.task_runtime is None and task == state.task:
        return
    proposed = task.model_copy(update={"version": task.version + 1})
    values = {"progress": proposed.model_dump(), "version": proposed.version,
              "status": proposed.status, "last_run_id": state.run_id, "updated_at": utc_now_db()}
    if task.version == 0:
        db.add(AgentTask(id=task.task_id, user_id=task.user_id, actor_id=task.actor_id,
                         session_id=task.session_id, **values))
        await db.flush()
    else:
        result = await db.execute(update(AgentTask).where(AgentTask.id == task.task_id,
            AgentTask.user_id == task.user_id, AgentTask.session_id == task.session_id,
            AgentTask.version == task.version).values(**values))
        if result.rowcount != 1:
            raise SessionLeaseError("任务进度版本已变化")
    run_key = state.run_id or "message:" + str(state.source_message_id or uuid.uuid4())
    for step in state.task_runtime.steps if state.task_runtime else []:
        db.add(AgentTaskStep(id=str(uuid.uuid4()), task_id=task.task_id, run_key=run_key, **step.model_dump()))
    state.task = proposed
