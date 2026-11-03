"""SQL轮次协调。进程任务只负责执行，Run/消息/事件/回执才是恢复事实。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress
from datetime import datetime, timedelta
import hashlib
import json
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import select, update, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.memory import SessionBusyError, SessionLeaseError
from app.agent.state import AgentState
from app.models.models import ChatRun, RunEvent, Session, SessionStatus, Message, MessageRole, User, OperationAudit, RefundAction
from app.security import ensure_owner_or_admin, authenticate_access_token

FINAL = {"succeeded", "awaiting_confirmation", "failed", "cancelled"}
LEASE_SECONDS = 45
RUN_TIMEOUT_SECONDS = 30
TASKS: dict[str, asyncio.Task] = {}


@asynccontextmanager
async def coordinated(manager, sid: str):
    """Redis获取前不可用可用SQL租约接管；已获得后失租必须中止。"""
    context = manager.session_lock(sid)
    try:
        assert_owner = await context.__aenter__()
    except SessionLeaseError:
        async def sql_only():
            return None
        yield sql_only
        return
    try:
        yield assert_owner
    finally:
        await context.__aexit__(None,None,None)


def fingerprint(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def append_event(db: AsyncSession, run: ChatRun, kind: str, payload: dict) -> None:
    run.sequence = (run.sequence or 0) + 1
    db.add(RunEvent(id=str(uuid.uuid4()), run_id=run.id, session_id=run.session_id,
                    sequence=run.sequence, event_type=kind, payload=payload))


def public_event(item: RunEvent) -> dict:
    return {**item.payload, "type": item.event_type, "run_id": item.run_id, "session_id": item.session_id,
            "event_id": item.id, "sequence": item.sequence, "schema_version": item.schema_version,
            "timestamp": item.created_at.isoformat() + "Z"}


async def guard_write(db: AsyncSession, source: Message) -> None:
    """与业务提交同一事务锁定会话fence。旧worker不能在失租/关闭后提交副作用。"""
    if not source.run_id:  # 迁移前的可信消息仍按原签名/权限约束处理。
        return
    run = await db.get(ChatRun, source.run_id, populate_existing=True)
    if run is None or run.status != "running":
        raise HTTPException(409, "执行轮次已失效，请核查原回执")
    if run.redis_lock_token:
        from app.utils.database import get_redis
        try:
            redis = await get_redis()
            valid = redis is not None and await redis.get("session_lock:"+run.session_id) == run.redis_lock_token
        except Exception:
            valid = False
        if not valid:
            raise HTTPException(409,"共享租约已失效，禁止继续提交")
    owned = await db.execute(update(Session).where(
        Session.id == run.session_id, Session.lease_token == run.lease_token,
        Session.lease_expires_at > datetime.utcnow(), Session.deleted_at.is_(None),
        Session.status == SessionStatus.ACTIVE).values(version=Session.version))
    if owned.rowcount != 1:
        raise HTTPException(409, "轮次租约或会话状态已失效，禁止继续提交")


async def accept(db: AsyncSession, actor: User, body) -> tuple[ChatRun, bool]:
    ensure_owner_or_admin(actor, body.user_id)
    if await db.get(User, body.user_id) is None:
        raise HTTPException(404, "用户不存在")
    cid = body.client_message_id or str(uuid.uuid4())
    sid = body.session_id or str(uuid.uuid5(uuid.NAMESPACE_URL, f"aicshub:{body.user_id}:{cid}"))
    session = await db.get(Session, sid)
    if session is None:
        if body.session_id:
            raise HTTPException(404, "会话不存在")
        session = Session(id=sid, user_id=body.user_id)
        db.add(session)
    else:
        ensure_owner_or_admin(actor, session.user_id)
        if session.user_id != body.user_id:
            raise HTTPException(403, "会话主体不匹配")
        if session.deleted_at is not None:
            raise HTTPException(404, "会话不存在")
        if session.status == SessionStatus.CLOSED:
            raise HTTPException(400, "会话已关闭")
    payload = dict(message=body.message, action_id=body.action_id, action_version=body.action_version)
    digest = fingerprint({**payload, "actor_id": actor.id, "user_id": body.user_id})
    from app.config.config import settings
    from app.services.llm_governance import stable_gray_assignment
    selected_mode=settings.REQUEST_UNDERSTANDING_MODE
    if selected_mode=='model' and not stable_gray_assignment(str(body.user_id),settings.REQUEST_UNDERSTANDING_PERCENTAGE):selected_mode='rules'
    payload["_understanding_mode"]=selected_mode
    payload["_strategy_version"]=settings.REQUEST_UNDERSTANDING_VERSION
    run = ChatRun(id=str(uuid.uuid4()), user_id=body.user_id, actor_id=actor.id, session_id=sid,
                  client_message_id=cid, request_hash=digest, request_data=payload,
                  lease_expires_at=datetime.utcnow() + timedelta(seconds=LEASE_SECONDS))
    db.add(run)
    append_event(db, run, "stream_start", {"status": "accepted"})
    try:
        await db.commit()
        return run, True
    except IntegrityError:
        await db.rollback()
        winner = await db.scalar(select(ChatRun).where(ChatRun.user_id == body.user_id,
                                  ChatRun.session_id == sid, ChatRun.client_message_id == cid))
        if winner is None or winner.request_hash != digest:
            raise HTTPException(409, "client_message_id已用于不同请求") from None
        return winner, False


async def _claim(factory: async_sessionmaker[AsyncSession], rid: str, token: str, access_token: str | None, redis_lock_token: str | None = None) -> tuple[ChatRun, int]:
    async with factory() as db:
        run = await db.get(ChatRun, rid)
        sid = run.session_id
        actor = await authenticate_access_token(access_token, db) if access_token else await db.get(User, run.actor_id)
        if actor is None or not actor.status or actor.disabled_at is not None:
            raise HTTPException(401, "身份已失效")
        ensure_owner_or_admin(actor, run.user_id)
        expires = datetime.utcnow() + timedelta(seconds=LEASE_SECONDS)
        result = await db.execute(update(Session).where(Session.id == run.session_id,
            Session.deleted_at.is_(None), Session.status == SessionStatus.ACTIVE,
            or_(Session.lease_token.is_(None), Session.lease_expires_at <= datetime.utcnow()))
            .values(lease_token=token, lease_expires_at=expires))
        if result.rowcount != 1:
            await db.rollback()
            session = await db.get(Session, sid)
            if session is None or session.deleted_at is not None:
                raise HTTPException(404, "会话不存在")
            if session.status == SessionStatus.CLOSED:
                raise HTTPException(400, "会话已关闭")
            raise SessionBusyError("当前会话正在处理其他消息，请稍后重试")
        updated = await db.execute(update(ChatRun).where(ChatRun.id == rid, ChatRun.status == "accepted")
                                   .values(status="running", lease_token=token, lease_expires_at=expires, redis_lock_token=redis_lock_token))
        if updated.rowcount != 1:
            await db.rollback()
            raise SessionBusyError("原轮次已由其他worker接管")
        session = await db.get(Session, run.session_id, populate_existing=True)
        message = Message(session_id=session.id, role=MessageRole.USER, content=run.request_data["message"], run_id=rid)
        db.add(message)
        await db.flush()
        run = await db.get(ChatRun, rid, populate_existing=True)
        run.user_message_id = message.id
        session.message_count += 1
        append_event(db, run, "run_status", {"status": "running", "coordination": "sql_lease"})
        await db.commit()
        return run, session.version


async def assert_fence(db: AsyncSession, run: ChatRun, token: str, version: int) -> None:
    result = await db.execute(update(Session).where(Session.id == run.session_id,
        Session.lease_token == token, Session.version == version, Session.lease_expires_at > datetime.utcnow(),
        Session.status == SessionStatus.ACTIVE, Session.deleted_at.is_(None)).values(version=Session.version))
    if result.rowcount != 1:
        raise SessionLeaseError("轮次所有权或会话版本已失效")


async def _renew(factory: async_sessionmaker[AsyncSession], run: ChatRun, token: str, assert_owner, owner: asyncio.Task) -> None:
    try:
        while True:
            await asyncio.sleep(10)
            await assert_owner()
            async with factory() as db:
                now = datetime.utcnow()
                result = await db.execute(update(Session).where(Session.id == run.session_id, Session.lease_token == token,
                    Session.lease_expires_at > now, Session.status == SessionStatus.ACTIVE, Session.deleted_at.is_(None))
                    .values(lease_expires_at=now + timedelta(seconds=LEASE_SECONDS)))
                if result.rowcount != 1:
                    raise SessionLeaseError("轮次租约已丢失")
                await db.execute(update(ChatRun).where(ChatRun.id == run.id, ChatRun.lease_token == token,
                    ChatRun.status == "running").values(lease_expires_at=now + timedelta(seconds=LEASE_SECONDS)))
                await db.commit()
    except asyncio.CancelledError:
        raise
    except Exception:
        owner.cancel()


async def execute(factory: async_sessionmaker[AsyncSession], rid: str, service, manager, access_token: str | None, streaming: bool) -> None:
    renewer = None
    gateway_token = None
    token = str(uuid.uuid4())
    try:
        async with factory() as db:
            original = await db.get(ChatRun, rid)
            sid = original.session_id
        async with coordinated(manager, sid) as assert_owner:
            run, version = await _claim(factory, rid, token, access_token, getattr(assert_owner,"redis_lock_token",None))
            renewer = asyncio.create_task(_renew(factory, run, token, assert_owner, asyncio.current_task()))
            state = await service.hydrate_state(sid, run.user_id, run.request_data["message"],
                action_id=run.request_data.get("action_id"), recover_from_db=True)
            state.source_message_id, state.actor_id = run.user_message_id, run.actor_id
            state.requested_action_id = run.action_id or run.request_data.get("action_id")
            state.requested_action_version = run.request_data.get("action_version")
            state.session_version = version
            state.understanding_mode=run.request_data.get("_understanding_mode","rules")
            state.strategy_version=run.request_data.get("_strategy_version","legacy")
            from app.services.model_gateway import CALL_CONTEXT
            run_deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
            gateway_token=CALL_CONTEXT.set({"user_id":run.user_id,"run_id":run.id,"factory":factory,
                "route_version":state.strategy_version,"deadline":run_deadline,"attempts":0})
            async with factory() as db:
                current = await db.get(ChatRun,rid)
                current.action_id = state.collected_info.get("refund_preview",{}).get("action_id")
                await db.commit()
            async with asyncio.timeout_at(run_deadline) as run_budget:
                if streaming:
                    # 文本和done只在最终SQL提交后发布；只保留有界阶段进度。
                    count = 0
                    agen = service.process_stream(state)
                    try:
                        async for event in agen:
                            if event.get("type") == "done":
                                state.reply = event.get("reply", state.reply)
                            elif event.get("type") in {"node_start", "stream_mode"} and count < 64:
                                async with factory() as db:
                                    current = await db.get(ChatRun, rid)
                                    await assert_fence(db, current, token, version)
                                    append_event(db, current, event["type"], {k: event[k] for k in ("node", "mode", "token_stream") if k in event})
                                    await db.commit()
                                count += 1
                    finally:
                        await agen.aclose()
                else:
                    state = await service.process(state)
            if run_budget.expired() or time.monotonic() >= run_deadline:
                raise TimeoutError("late_run_result_rejected")
            await assert_owner()
            async with factory() as db:
                current = await db.get(ChatRun, rid)
                await assert_fence(db, current, token, version)
                if current.status != "running":
                    raise SessionLeaseError("轮次已停止")
                if time.monotonic() >= run_deadline:
                    raise TimeoutError("late_run_result_rejected")
                session = await db.get(Session, sid, populate_existing=True)
                current.action_id = state.collected_info.get("refund_preview",{}).get("action_id") or current.action_id
                message, _ = await service.persist_result(db, session, state, run.request_data["message"], rid, commit=False)
                message.run_id = rid
                state.session_version = version + 1
                session.version, session.lease_token, session.lease_expires_at = version + 1, None, None
                # 只保存下一轮可复用的必要参数；不持久旧证据/草稿/确认标记。
                clean = state.for_next_turn(run.user_id, "")
                session.context_data = {"collected_info": clean.collected_info, "pending_confirmations": clean.pending_confirmations,
                                        "missing_slots": state.missing_slots, "needs_clarification": state.needs_clarification}
                finish(db, current, message, state)
                from app.services.background_jobs import enqueue
                enqueue(db, "summary", sid, run.user_id, f"summary:{sid}:{version+1}")
                await assert_owner()
                if time.monotonic() >= run_deadline:
                    raise TimeoutError("late_run_result_rejected")
                await db.commit()
            # 缓存/摘要失败不能撤销已经提交的最终结果。
            with suppress(Exception):
                await manager.save_state(state)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        # 提交窗口异常保留reconciling；恢复器只读业务回执，绝不重做图中的写操作。
        with suppress(Exception):
            async with factory() as db:
                run = await db.get(ChatRun, rid)
                if run and run.status not in FINAL and (run.lease_token in (None, token)):
                    run.status = "reconciling" if run.user_message_id else "failed"
                    run.error_code = str(exc.status_code) if isinstance(exc, HTTPException) else (
                        "409" if isinstance(exc, SessionBusyError) else "503")
                    run.lease_expires_at = datetime.utcnow()
                    append_event(db, run, "run_status", {"status": run.status})
                    await db.execute(update(Session).where(Session.id == run.session_id, Session.lease_token == token)
                                     .values(lease_expires_at=datetime.utcnow()))
                    await db.commit()
    finally:
        if gateway_token is not None:
            from app.services.model_gateway import CALL_CONTEXT
            CALL_CONTEXT.reset(gateway_token)
        if renewer:
            renewer.cancel()
            with suppress(asyncio.CancelledError):
                await renewer


def finish(db: AsyncSession, run: ChatRun, message: Message, state: AgentState) -> None:
    run.status = "awaiting_confirmation" if state.pending_confirmations else "succeeded"
    run.assistant_message_id = message.id
    run.result = dict(run_id=run.id, run_status=run.status, message_id=message.id, session_id=run.session_id,
                      reply=state.reply, intent=state.detected_intent, response_time_ms=state.execution_time_ms, trace_id=run.id)
    run.updated_at = datetime.utcnow()
    append_event(db, run, "token", {"content": state.reply, "provider": "validated"})
    append_event(db, run, "done", {"reply": state.reply, "intent": state.detected_intent})
    append_event(db, run, "stream_end", {"message_id": message.id, "status": run.status})


async def recover(factory: async_sessionmaker[AsyncSession], rid: str) -> None:
    """孤儿恢复只查原回执；无可证明结果时失败/unknown，不再调用模型或写工具。"""
    from app.services.ticket_creation import _digest, handoff_receipt, handoff_message
    from app.models.models import Ticket, HandoffRequestRecord
    async with factory() as db:
        run = await db.get(ChatRun, rid)
        if run is None or run.status in FINAL or (run.lease_expires_at and run.lease_expires_at > datetime.utcnow()):
            return
        session = await db.scalar(select(Session).where(Session.id == run.session_id).with_for_update())
        run = await db.scalar(select(ChatRun).where(ChatRun.id == rid).with_for_update().execution_options(populate_existing=True))
        if run.status in FINAL or (run.lease_expires_at and run.lease_expires_at > datetime.utcnow()):
            return
        if session is None or session.user_id != run.user_id or session.deleted_at is not None or session.status == SessionStatus.CLOSED:
            run.status = "cancelled"
            append_event(db, run, "error", {"code": 409, "message": "会话已关闭，原业务回执仍可独立核查"})
            await db.commit()
            return
        # 不抢走其他Run的新租约。
        if session.lease_token and session.lease_token != run.lease_token and session.lease_expires_at > datetime.utcnow():
            return
        receipt_parts = []
        receipt_evidence = []
        for operation, prefix in (("create_ticket", "chat-ticket"), ("request_handoff", "chat-handoff")):
            key = "ticket-v1:" + _digest([operation, run.actor_id, run.user_id, f"{prefix}:{run.session_id}:{run.user_message_id}"])
            audit = await db.scalar(select(OperationAudit).where(OperationAudit.idempotency_key == key))
            if audit and audit.user_id == run.user_id and audit.status == "succeeded":
                model = Ticket if operation == "create_ticket" else HandoffRequestRecord
                item = await db.get(model, audit.resource_id)
                if item:
                    receipt_evidence.append({"success": True, "data": {"id": item.id, "status": item.status.value}})
                    receipt_parts.append(f"工单 {item.id} 已登记，状态：{item.status.value}。" if operation == "create_ticket" else handoff_message(handoff_receipt(item)))
        # action可由本轮创建或显式确认；仅submitted动作证明已提交，pending绝不自动确认。
        action_id = run.action_id or run.request_data.get("action_id")
        action = await db.get(RefundAction, action_id) if action_id else None
        from app.agent.refund_authorization import CONFIRMATIONS
        if (action and run.request_data["message"].strip() in CONFIRMATIONS
                and action.user_id == run.user_id and action.session_id == run.session_id and action.status == "submitted"):
            audit = await db.scalar(select(OperationAudit).where(OperationAudit.operation == "apply_refund", OperationAudit.resource_id == action.application_id))
            if audit and audit.user_id == run.user_id and audit.status == "succeeded":
                receipt_evidence.append({"success": True, "data": {"refund_id": action.application_id}})
                from app.agent.nodes.response_node import build_refund_reply_template
                receipt_parts.append(build_refund_reply_template(audit.result_payload))
        if receipt_parts:
            # 恢复模板也经过响应节点的独立校验；不调用模型、不重做业务工具。
            from types import SimpleNamespace
            from app.agent.validation import create_validator
            from app.agent.nodes.response_node import validate_candidate
            from app.agent.state import ToolCall
            restored = AgentState(session_id=run.session_id, user_id=run.user_id,
                                  user_message=run.request_data["message"], reply="\n".join(receipt_parts))
            restored.tool_calls = [ToolCall(tool_name="persisted_receipt", arguments={}, result=evidence, success=True)
                                   for evidence in receipt_evidence]
            gate = SimpleNamespace(validator=create_validator(), _rag_judge_applicable=lambda state: False)
            if not await validate_candidate(gate, restored):
                run.status, run.error_code = "failed", "recovery_validation_failed"
                append_event(db, run, "error", {"code":503,"message":"原回执存在，但恢复文本未通过校验，请独立查询业务记录"})
                session.lease_token, session.lease_expires_at = None, None
                await db.commit()
                return
            message = Message(session_id=run.session_id, run_id=rid, role=MessageRole.ASSISTANT, content="\n".join(receipt_parts))
            db.add(message)
            await db.flush()
            session.message_count += 1
            session.version += 1
            # 回执已证明原动作提交，不能保留它在上轮SQL投影中的待确认标记。
            projection = dict(session.context_data or {})
            info = dict(projection.get("collected_info") or {})
            if action and action.status == "submitted" and info.get("refund_preview", {}).get("action_id") == action.id:
                for key in ("refund_preview", "refund_action", "confirm_refund"):
                    info.pop(key, None)
                projection["collected_info"] = info
                projection["pending_confirmations"] = [value for value in projection.get("pending_confirmations", []) if value != "apply_refund"]
                session.context_data = projection
            state = AgentState(session_id=run.session_id, user_id=run.user_id, user_message="", reply=message.content)
            append_event(db,run,"run_status",{"status":"recovered_from_persisted_receipt","model_called":False})
            finish(db, run, message, state)
        else:
            # 已知无副作用也不伪造答案。用户可显式新轮次重试；原用户消息不重复。
            run.status, run.error_code = "failed", "interrupted_unknown"
            append_event(db, run, "error", {"code": 503, "message": "本轮中断，未确认最终结果；请查询业务记录后发起新轮次"})
        session.lease_token, session.lease_expires_at = None, None
        await db.commit()


async def wait(factory: async_sessionmaker[AsyncSession], rid: str) -> dict:
    deadline = asyncio.get_running_loop().time() + LEASE_SECONDS + 2
    while True:
        async with factory() as db:
            run = await db.get(ChatRun, rid)
            if run.status in FINAL:
                if run.result:
                    return run.result
                code = int(run.error_code) if run.error_code and run.error_code.isdecimal() else 503
                raise HTTPException(code, {400:"会话已关闭",401:"身份已失效",403:"无权执行该轮次",404:"会话不存在",409:"当前会话正在处理其他消息，请稍后重试"}.get(code,"本轮未完成，原请求不会自动重做；请查询运行状态"))
            expired = run.lease_expires_at and run.lease_expires_at <= datetime.utcnow()
        if expired:
            await recover(factory, rid)
        if asyncio.get_running_loop().time() >= deadline:
            raise HTTPException(503, "轮次仍在处理或对账，请使用run_id查询")
        await asyncio.sleep(0.05)


def start(factory: async_sessionmaker[AsyncSession], run: ChatRun, service, manager, access_token: str | None, streaming: bool) -> None:
    if run.status == "accepted" and run.id not in TASKS:
        if len(TASKS) >= 64:
            raise HTTPException(429, "执行队列已满，已接受Run可稍后用相同client_message_id查询；请勿更换ID重复提交")
        task = asyncio.create_task(execute(factory, run.id, service, manager, access_token, streaming))
        TASKS[run.id] = task
        task.add_done_callback(lambda _: TASKS.pop(run.id, None))
