"""Run租约保护的能力派发账本；中断只恢复目标和对账线索，不重放写操作。"""
from contextvars import ContextVar
from datetime import datetime
import hashlib
import json
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.models import AgentCapabilityCall, ChatRun

JOURNAL: ContextVar = ContextVar("capability_journal", default=None)
TASK_STATE: ContextVar = ContextVar("capability_task_state", default=None)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


class CapabilityJournal:
    def __init__(self, factory: async_sessionmaker[AsyncSession], run_id: str, token: str, version: int):
        self.factory, self.run_id, self.token, self.version = factory, run_id, token, version

    async def _owned(self, db: AsyncSession) -> ChatRun:
        from app.services.run_execution import assert_fence
        from app.agent.memory import SessionLeaseError
        run = await db.get(ChatRun, self.run_id)
        if (run is None or run.status != "running" or run.lease_token != self.token
                or not run.lease_expires_at or run.lease_expires_at <= datetime.utcnow()):
            raise SessionLeaseError("能力调用所属Run租约已失效")
        await assert_fence(db, run, self.token, self.version)
        return run

    async def begin(self, capability: str, payload: dict, effect: str) -> str:
        from app.agent.memory import SessionLeaseError
        state = TASK_STATE.get()
        # Credentials are never included in storage or audit digests.
        input_digest = digest({k: v for k, v in payload.items() if k != "authorization"})
        call_key = digest([capability, input_digest])
        async with self.factory() as db:
            run = await self._owned(db)
            if await db.scalar(select(AgentCapabilityCall.id).where(
                    AgentCapabilityCall.run_id == run.id, AgentCapabilityCall.call_key == call_key)):
                raise ValueError("capability_already_dispatched")
            if state and (state.run_id != run.id or state.user_id != run.user_id
                          or state.actor_id != run.actor_id or state.session_id != run.session_id):
                raise SessionLeaseError("能力任务主体与Run不匹配")
            call_id = str(uuid.uuid4())
            db.add(AgentCapabilityCall(id=call_id, run_id=run.id, task_id=state.task.task_id if state and state.task else None,
                user_id=run.user_id, actor_id=run.actor_id, session_id=run.session_id, capability=capability,
                effect=effect, call_key=call_key, input_digest=input_digest, status="running",
                resource_id=payload.get("order_id") or payload.get("action_id") or payload.get("ticket_id"),
                task_progress=state.task.model_dump() if state and state.task else None))
            await db.commit()
            return call_id

    async def verify_domain_scope(self, db: AsyncSession, actor_id: int, payload: dict) -> None:
        from app.agent.memory import SessionLeaseError
        run = await self._owned(db)
        if (run.actor_id != actor_id or run.user_id != payload.get("user_id")
                or run.session_id != payload.get("session_id")):
            raise SessionLeaseError("业务动作与能力Run主体不一致")

    async def finish(self, call_id: str, status: str, result: object = None, *, error: str | None = None,
                     resource_id: str | None = None) -> None:
        if status not in {"succeeded", "failed", "rejected", "unknown"}:
            raise ValueError("invalid_capability_outcome")
        async with self.factory() as db:
            await self._owned(db)
            changed = await db.execute(update(AgentCapabilityCall).where(AgentCapabilityCall.id == call_id,
                AgentCapabilityCall.run_id == self.run_id, AgentCapabilityCall.status == "running").values(
                    status=status, result_digest=digest(result), error_code=error,
                    **({"resource_id": resource_id} if resource_id else {}), finished_at=datetime.utcnow()))
            if changed.rowcount != 1:
                raise ValueError("capability_receipt_already_recorded")
            await db.commit()


async def settle_open_calls(db: AsyncSession, run: ChatRun) -> None:
    """被节点捕获的取消/超时也须收口；不能在成功结束的Run留下永久running。"""
    from sqlalchemy import case
    await db.execute(update(AgentCapabilityCall).where(AgentCapabilityCall.run_id == run.id,
        AgentCapabilityCall.user_id == run.user_id, AgentCapabilityCall.actor_id == run.actor_id,
        AgentCapabilityCall.session_id == run.session_id, AgentCapabilityCall.status == "running").values(
            status=case((AgentCapabilityCall.effect == "write", "unknown"), else_="failed"),
            error_code="capability_interrupted", finished_at=datetime.utcnow()))


async def recover_calls(db: AsyncSession, run: ChatRun) -> dict:
    """调用方已锁定原会话/Run；只终结孤儿记录，不启动模型或能力。"""
    from app.agent.task_contracts import TaskProgress
    from app.models.models import AgentTask, AgentTaskStep, OperationAudit, RefundAction
    rows = (await db.execute(select(AgentCapabilityCall).where(AgentCapabilityCall.run_id == run.id)
                             .order_by(AgentCapabilityCall.created_at, AgentCapabilityCall.id))).scalars().all()
    unknown_write = False
    previews = {}
    snapshots = {}
    for row in rows:
        if row.user_id != run.user_id or row.actor_id != run.actor_id or row.session_id != run.session_id:
            raise ValueError("capability_recovery_owner_mismatch")
        if row.capability == "prepare_refund":
            from app.services.refund_actions import preview_request_key
            key = preview_request_key(run.actor_id, run.user_id, run.session_id, "task-preview:" + run.id)
            audit = await db.scalar(select(OperationAudit).where(OperationAudit.idempotency_key == key,
                OperationAudit.operation == "prepare_refund", OperationAudit.user_id == run.user_id,
                OperationAudit.status == "succeeded"))
            action = await db.get(RefundAction, audit.resource_id) if audit else None
            if (action and action.user_id == run.user_id and action.actor_id == run.actor_id
                    and action.session_id == run.session_id):
                row.resource_id, row.status, row.error_code = action.id, "succeeded", None
                row.finished_at = row.finished_at or datetime.utcnow()
                run.action_id = action.id
                if action.status == "pending" and action.expires_at > datetime.utcnow():
                    previews[row.task_id] = action.id
        if row.status == "running":
            row.status = "unknown" if row.effect == "write" else "failed"
            row.error_code, row.finished_at = "run_interrupted", datetime.utcnow()
        unknown_write |= row.effect == "write" and row.status == "unknown"
        if row.task_id and row.task_progress:
            try:
                task = TaskProgress.model_validate(row.task_progress)
            except ValueError:
                continue
            if (task.task_id == row.task_id and task.user_id == run.user_id and task.actor_id == run.actor_id
                    and task.session_id == run.session_id):
                snapshots[task.task_id] = task
    restored = []
    for task_id, task in snapshots.items():
        persisted = await db.get(AgentTask, task_id)
        # Never overwrite a later Run's progress or a finalized task.
        if persisted and (persisted.version != task.version or persisted.status not in {
                "running", "waiting_input", "waiting_confirmation"}):
            continue
        status = "stopped" if unknown_write else "waiting_confirmation" if task_id in previews else "waiting_input"
        value = task.model_copy(update={"status": status, "waiting_for": None if unknown_write else
            "refund_confirmation" if task_id in previews else "request", "action_id": previews.get(task_id, task.action_id),
            "stop_reason": "interrupted_outcome_unknown" if unknown_write else "interrupted_needs_fresh_evidence",
            "version": (persisted.version if persisted else 0) + 1})
        if persisted:
            persisted.progress, persisted.status, persisted.version = value.model_dump(), value.status, value.version
            persisted.last_run_id, persisted.updated_at = run.id, datetime.utcnow()
        else:
            db.add(AgentTask(id=task_id, user_id=run.user_id, actor_id=run.actor_id, session_id=run.session_id,
                last_run_id=run.id, progress=value.model_dump(), status=value.status, version=value.version))
            await db.flush()
        for sequence, row in enumerate((r for r in rows if r.task_id == task_id), 1):
            exists = await db.scalar(select(AgentTaskStep.id).where(AgentTaskStep.task_id == task_id,
                AgentTaskStep.run_key == run.id, AgentTaskStep.sequence == sequence))
            if not exists:
                db.add(AgentTaskStep(id=str(uuid.uuid4()), task_id=task_id, run_key=run.id, sequence=sequence,
                    action="prepare_refund" if row.capability == "prepare_refund" else "search_knowledge"
                    if row.capability == "hybrid_search" else "read", capability=row.capability,
                    status=row.status, input_digest=row.input_digest, error_code=row.error_code))
        restored.append(task_id)
    return {"calls": len(rows), "unknown_write": unknown_write, "restored_tasks": restored,
            "tools_replayed": False, "old_evidence_restored": False}
