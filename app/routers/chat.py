import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.utils.database import get_db
from app.models.models import (
    Session, SessionStatus, Message, MessageRole, User, AgentTrace, EvaluationResult
)
from app.schemas.schemas import ChatRequest, ChatResponse, APIResponse
from app.agent.graph import AgentGraph
from app.agent.memory import session_manager, SessionBusyError, SessionLeaseError
from app.utils.tracking import structured_logger, generate_trace_id, get_tracer
from app.security import get_current_user, ensure_owner_or_admin, enforce_rate_limit, require_user, authenticate_access_token
from app.config.config import settings
from app.services.chat_service import chat_service
from app.services.session_creation import create_session as create_session_record

router = APIRouter(prefix="/api/v1/chat", tags=["对话管理"])
WS_SEND_TIMEOUT_SECONDS = 5

agent_graph = AgentGraph()
chat_service.agent_graph = agent_graph

INTENT_LABELS = {
    "query_order": "订单查询",
    "refund": "退换货",
    "complaint": "投诉",
    "technical": "技术咨询",
    "promotion": "活动咨询",
    "human": "转人工",
    "general": "通用对话",
}


def _intent_label(intent):
    if not intent:
        return "通用对话"
    return INTENT_LABELS.get(intent, intent)


def _fmt_utc(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat()


def _build_rule_summary(messages: list[Message], intent: str | None = None) -> str:
    """无 LLM 时的可复现摘要：仅保留用户目标、订单号、已完成动作和待办。"""
    user_parts = [m.content.strip() for m in messages if m.role == MessageRole.USER and m.content.strip()][-3:]
    assistant_parts = [m.content.strip() for m in messages if m.role == MessageRole.ASSISTANT and m.content.strip()][-2:]
    target = "；".join(user_parts)
    done = "；".join(assistant_parts)
    return f"意图：{intent or 'general'}。用户目标：{target[:300]}。已完成/回复：{done[:300]}。待办：请根据最新消息继续处理。"


@router.post("/sessions", response_model=APIResponse)
async def create_session(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建新会话"""
    session = await create_session_record(db, current_user, user_id)

    return APIResponse(
        code=0,
        message="会话创建成功",
        data={
            "id": session.id,
            "status": SessionStatus.ACTIVE.value,
            "message_count": 0,
            "last_intent": None,
            "updated_at": _fmt_utc(session.updated_at),
        },
    )


@router.patch("/sessions/{session_id}/close", response_model=APIResponse)
async def close_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """关闭会话"""
    session = await db.get(Session, session_id)
    if not session or session.deleted_at is not None:
        raise HTTPException(status_code=404, detail="会话不存在")
    ensure_owner_or_admin(current_user, session.user_id)

    session.status = SessionStatus.CLOSED
    session.closed_at = datetime.utcnow()
    session.version += 1
    session.lease_token, session.lease_expires_at = None, None
    await db.commit()

    return APIResponse(
        code=0,
        message="会话已关闭",
        data={"id": session_id, "status": SessionStatus.CLOSED.value},
    )


@router.delete("/sessions/{session_id}", response_model=APIResponse)
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除会话（级联删除相关数据）"""
    from sqlalchemy import delete as sql_delete
    
    session = await db.get(Session, session_id)
    if not session or session.deleted_at is not None:
        raise HTTPException(status_code=404, detail="会话不存在")
    ensure_owner_or_admin(current_user, session.user_id)

    session.deleted_at = datetime.utcnow()
    session.status = SessionStatus.CLOSED
    session.closed_at = datetime.utcnow()
    session.version += 1
    session.lease_token, session.lease_expires_at = None, None
    await db.commit()

    return APIResponse(
        code=0,
        message="会话已删除",
        data={"id": session_id},
    )


@router.get("/sessions/{user_id}", response_model=APIResponse)
async def get_user_sessions(
    user_id: int,
    page: int = 1,
    page_size: int = 20,
    include_closed: bool = True,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_owner_or_admin(current_user, user_id)
    query = select(Session).where(Session.user_id == user_id, Session.deleted_at.is_(None))
    if not include_closed:
        query = query.where(Session.status == SessionStatus.ACTIVE)
    query = query.order_by(Session.updated_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    sessions = result.scalars().all()

    data = []
    for s in sessions:
        last_msg_query = select(Message).where(Message.session_id == s.id)
        last_msg_query = last_msg_query.order_by(Message.created_at.desc()).limit(1)
        last_msg_result = await db.execute(last_msg_query)
        last_msg = last_msg_result.scalar_one_or_none()

        title = _intent_label(s.last_intent)
        preview = last_msg.content[:50] if last_msg else ""
        if not s.last_intent and last_msg:
            title = last_msg.content[:20] or "通用对话"

        data.append({
            "id": s.id,
            "status": s.status.value,
            "message_count": s.message_count,
            "last_intent": s.last_intent,
            "title": title,
            "preview": preview,
            "updated_at": _fmt_utc(s.updated_at),
        })

    return APIResponse(
        code=0,
        message="获取成功",
        data=data,
    )


@router.post("/send", response_model=ChatResponse)
async def send_message(
    chat_data: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.run_execution import accept, start, wait
    from sqlalchemy.ext.asyncio import async_sessionmaker
    enforce_rate_limit(f"chat:{current_user.id}", settings.CHAT_RATE_LIMIT_PER_MINUTE)
    run, _ = await accept(db, current_user, chat_data)
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    scheme, _, credential = request.headers.get("authorization", "").partition(" ")
    start(factory, run, chat_service, session_manager, credential.strip() if scheme.lower()=="bearer" else None, False)
    result = await wait(factory, run.id)
    return ChatResponse(**result)


@router.get("/history/{session_id}", response_model=APIResponse)
async def get_chat_history(
    session_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await db.get(Session, session_id)
    if not session or session.deleted_at is not None:
        raise HTTPException(status_code=404, detail="会话不存在")
    ensure_owner_or_admin(current_user, session.user_id)

    query = select(Message).where(Message.session_id == session_id)
    query = query.order_by(Message.id.desc(), Message.created_at.desc()).limit(limit)
    result = await db.execute(query)
    messages = result.scalars().all()

    return APIResponse(
        code=0,
        message="获取成功",
        data={
            "session_id": session_id,
            "messages": [
                {
                    "id": m.id,
                    "role": m.role.value,
                    "content": m.content,
                    "token_count": m.token_count,
                    "response_time_ms": m.response_time_ms,
                    "created_at": _fmt_utc(m.created_at),
                }
                for m in reversed(messages)
            ],
        },
    )


@router.get("/tools", response_model=APIResponse)
async def get_available_tools(_: User = Depends(require_user)):
    """获取所有可用工具列表"""
    from app.agent.tools import get_all_tools

    tools = get_all_tools()
    return APIResponse(
        code=0,
        message="获取成功",
        data=[
            {
                "name": t.name,
                "description": t.description,
                "parameters": [
                    {
                        "name": p["name"],
                        "type": p["type"],
                        "description": p["description"],
                        "required": p.get("required", True),
                    }
                    for p in t.parameters
                ],
            }
            for t in tools
        ],
    )


@router.get("/intents", response_model=APIResponse)
async def get_intent_types(_: User = Depends(require_user)):
    """获取支持的意图类型"""
    intents = [
        {"code": "query_order", "name": "订单查询", "description": "查询订单状态与物流"},
        {"code": "refund", "name": "退换货", "description": "申请退换货"},
        {"code": "complaint", "name": "投诉", "description": "用户投诉处理"},
        {"code": "technical", "name": "技术咨询", "description": "产品使用与技术问题"},
        {"code": "promotion", "name": "活动咨询", "description": "优惠活动咨询"},
        {"code": "human", "name": "转人工", "description": "转接人工客服"},
        {"code": "general", "name": "通用", "description": "普通咨询"},
    ]
    return APIResponse(code=0, message="获取成功", data=intents)


@router.websocket("/stream")
async def websocket_chat_stream(websocket: WebSocket):
    """传输只订阅持久Run事件；socket断开不会取消已接受的执行。"""
    import asyncio
    from pydantic import ValidationError
    from app.services.run_execution import accept, start, wait, public_event, FINAL
    from app.models.models import ChatRun, RunEvent
    from app.utils.database import async_session
    from sqlalchemy.ext.asyncio import async_sessionmaker
    scheme, _, credential = websocket.headers.get("authorization", "").partition(" ")
    token = credential.strip() if scheme.lower()=="bearer" else websocket.query_params.get("access_token")
    try:
        async with async_session() as db:
            await authenticate_access_token(token or "",db)
    except HTTPException:
        await websocket.close(code=4401,reason="身份已失效")
        return
    except SQLAlchemyError:
        await websocket.close(code=1013,reason="认证服务暂时不可用")
        return
    await websocket.accept()
    async def emit(payload):
        await asyncio.wait_for(websocket.send_json(payload),timeout=WS_SEND_TIMEOUT_SECONDS)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                async with async_session() as db:
                    actor = await authenticate_access_token(token,db)
            except HTTPException as exc:
                await emit({"type":"error","code":401,"message":exc.detail})
                await websocket.close(code=4401)
                return
            try:
                data=json.loads(raw)
                if not isinstance(data,dict) or not isinstance(data.get("message"),str):
                    raise ValueError("invalid message")
                if data.get("user_id") is not None and (type(data["user_id"]) is not int or data["user_id"]<=0):
                    raise ValueError("invalid identity")
                if data.get("user_id",actor.id)!=actor.id:
                    raise HTTPException(403,"无权使用其他用户身份")
                body=ChatRequest.model_validate({**data,"user_id":actor.id})
                if body.session_id is not None and not isinstance(data.get("session_id"),str):
                    raise ValueError("invalid session")
                enforce_rate_limit(f"chat:{actor.id}",settings.CHAT_RATE_LIMIT_PER_MINUTE)
                async with async_session() as db:
                    run,_=await accept(db,actor,body)
                    factory=async_sessionmaker(db.bind,expire_on_commit=False)
                start(factory,run,chat_service,session_manager,token,True)
                # 取得执行资格前的拒绝保持旧协议首帧error，不把授权拒绝当已开始。
                while True:
                    async with factory() as db:
                        current=await db.get(ChatRun,run.id)
                        phase=current.status
                    if phase in FINAL:
                        await wait(factory,run.id)
                        break
                    if phase != "accepted": break
                    await asyncio.sleep(0.01)
                async with factory() as db:
                    initial=await db.scalar(select(RunEvent).where(RunEvent.run_id==run.id,RunEvent.sequence==1))
                    await emit(public_event(initial))
                result=await wait(factory,run.id)
                async with factory() as db:
                    events=(await db.execute(select(RunEvent).where(RunEvent.run_id==run.id,RunEvent.sequence>1)
                                            .order_by(RunEvent.sequence))).scalars().all()
                    for event in events:
                        await emit(public_event(event))
            except (json.JSONDecodeError,ValidationError,ValueError):
                await emit({"type":"error","code":400,"message":"消息参数无效"})
            except (SessionBusyError,SessionLeaseError) as exc:
                await emit({"type":"error","code":409 if isinstance(exc,SessionBusyError) else 503,"message":"会话协调服务暂时不可用"})
            except HTTPException as exc:
                await emit({"type":"error","code":exc.status_code,"message":exc.detail})
                if exc.status_code==401:
                    await websocket.close(code=4401)
                    return
    except (WebSocketDisconnect, asyncio.TimeoutError, OSError):
        pass
    except SQLAlchemyError:
        with __import__("contextlib").suppress(Exception):
            await emit({"type":"error","code":503,"message":"数据库暂时不可用，请查询原Run"})
    finally:
        with __import__("contextlib").suppress(Exception):
            await websocket.close()


@router.get("/runs/{run_id}")
async def get_run(run_id: str, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_user)):
    from app.models.models import ChatRun
    from app.services.run_execution import recover
    from sqlalchemy.ext.asyncio import async_sessionmaker
    run=await db.get(ChatRun,run_id)
    if run is None: raise HTTPException(404,"Run不存在")
    ensure_owner_or_admin(current_user,run.user_id)
    session=await db.get(Session,run.session_id)
    if session is None or session.deleted_at is not None: raise HTTPException(404,"会话不存在")
    await db.commit()
    await recover(async_sessionmaker(db.bind,expire_on_commit=False),run_id)
    await db.refresh(run)
    return {"code":0,"data":{"run_id":run.id,"session_id":run.session_id,"status":run.status,
            "sequence":run.sequence,"result":run.result,"error_code":run.error_code}}


@router.get("/tasks/{task_id}", response_model=APIResponse)
async def get_agent_task(task_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """仅返回本人任务进度和能力审计，不返回旧证据或内部确认凭据。"""
    from app.models.models import AgentTask, AgentTaskStep
    task = await db.get(AgentTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    ensure_owner_or_admin(current_user, task.user_id)
    session = await db.get(Session, task.session_id)
    if session is None or session.deleted_at is not None or session.user_id != task.user_id:
        raise HTTPException(404, "会话不存在")
    steps = (await db.execute(select(AgentTaskStep).where(AgentTaskStep.task_id == task.id)
        .order_by(AgentTaskStep.created_at.desc(), AgentTaskStep.sequence.desc()).limit(30))).scalars().all()
    return APIResponse(code=0, message="任务进度", data={"task_id": task.id, "session_id": task.session_id,
        "status": task.status, "version": task.version, "last_run_id": task.last_run_id,
        "goal": task.progress.get("goal"), "waiting_for": task.progress.get("waiting_for"),
        "stop_reason": task.progress.get("stop_reason"),
        "items": [{"item_id": item.get("item_id"), "text": item.get("text"), "kind": item.get("kind"),
                   "source_message_id": item.get("source_message_id"),
                   "status": task.progress.get("item_outcomes", {}).get(item.get("item_id"), "pending")}
                  for item in task.progress.get("items", [])],
        "steps": [{"action": s.action, "capability": s.capability, "status": s.status,
                   "sequence": s.sequence, "run_key": s.run_key, "error_code": s.error_code} for s in reversed(steps)]})


@router.get("/sessions/{session_id}/runs/{client_message_id}")
async def resolve_run(session_id: str, client_message_id: str, db: AsyncSession=Depends(get_db),
                      current_user: User=Depends(get_current_user)):
    from app.models.models import ChatRun
    session=await db.get(Session,session_id)
    if session is None or session.deleted_at is not None: raise HTTPException(404,"会话不存在")
    ensure_owner_or_admin(current_user,session.user_id)
    run=await db.scalar(select(ChatRun).where(ChatRun.session_id==session_id,ChatRun.user_id==session.user_id,
                                            ChatRun.client_message_id==client_message_id))
    if run is None: raise HTTPException(404,"尚未接受该客户端消息")
    return {"code":0,"data":{"run_id":run.id,"status":run.status}}


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, after: int=0, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_user)):
    from app.models.models import ChatRun,RunEvent
    from app.services.run_execution import public_event
    run=await db.get(ChatRun,run_id)
    if run is None: raise HTTPException(404,"Run不存在")
    ensure_owner_or_admin(current_user,run.user_id)
    session=await db.get(Session,run.session_id)
    if session is None or session.deleted_at is not None: raise HTTPException(404,"会话不存在")
    if after<0: raise HTTPException(400,"游标必须非负")
    rows=(await db.execute(select(RunEvent).where(RunEvent.run_id==run_id,RunEvent.sequence>after)
                          .order_by(RunEvent.sequence).limit(100))).scalars().all()
    return {"code":0,"data":[public_event(row) for row in rows]}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, db: AsyncSession=Depends(get_db), current_user: User=Depends(get_current_user)):
    from sqlalchemy import update
    from app.models.models import ChatRun,RefundAction
    from app.services.run_execution import append_event
    run=await db.scalar(select(ChatRun).where(ChatRun.id==run_id).with_for_update())
    if run is None: raise HTTPException(404,"Run不存在")
    ensure_owner_or_admin(current_user,run.user_id)
    if run.status not in {"accepted","awaiting_confirmation","cancelled"}:
        raise HTTPException(409,"执行已开始或已完成，断线不能推定取消，请查询回执")
    if run.status != "cancelled":
        if run.status=="awaiting_confirmation":
            await db.execute(update(RefundAction).where(RefundAction.id==run.action_id,RefundAction.session_id==run.session_id,
                RefundAction.user_id==run.user_id,RefundAction.status=="pending").values(status="cancelled"))
        run.status="cancelled"
        append_event(db,run,"run_status",{"status":"cancelled"})
        await db.commit()
    return {"code":0,"data":{"run_id":run.id,"status":run.status}}
