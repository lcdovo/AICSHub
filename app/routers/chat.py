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
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    ensure_owner_or_admin(current_user, session.user_id)

    session.status = SessionStatus.CLOSED
    session.closed_at = datetime.utcnow()
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
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    ensure_owner_or_admin(current_user, session.user_id)

    # 先删除关联的AgentTrace记录
    await db.execute(
        sql_delete(AgentTrace).where(AgentTrace.session_id == session_id)
    )
    
    # 再删除关联的EvaluationResult记录
    await db.execute(
        sql_delete(EvaluationResult).where(EvaluationResult.session_id == session_id)
    )
    
    # 删除会话（会级联删除messages）
    await db.delete(session)
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
    query = select(Session).where(Session.user_id == user_id)
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
    request_trace_id = getattr(getattr(request, "state", None), "trace_id", None) or generate_trace_id()
    enforce_rate_limit(f"chat:{current_user.id}", settings.CHAT_RATE_LIMIT_PER_MINUTE)
    ensure_owner_or_admin(current_user, chat_data.user_id)
    user = await db.get(User, chat_data.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    session_id = chat_data.session_id
    if session_id:
        session = await db.get(Session, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        ensure_owner_or_admin(current_user, session.user_id)
        if session.status == SessionStatus.CLOSED:
            raise HTTPException(status_code=400, detail="会话已关闭")
    else:
        session_id = str(uuid.uuid4())
        session = Session(
            id=session_id,
            user_id=chat_data.user_id,
            status=SessionStatus.ACTIVE,
        )
        db.add(session)
        await db.commit()

    # End the pre-lock read transaction so refresh sees the previous turn's commit.
    await db.commit()
    try:
        async with session_manager.session_lock(session_id) as assert_owner:
            # 生产请求已通过get_current_user；重新验证等待期间的撤销/到期与角色变化。
            # 离线用例可显式覆盖认证依赖，此时没有HTTP Bearer凭据。
            scheme, _, credential = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() == "bearer" and credential.strip():
                current_user = await authenticate_access_token(credential.strip(), db)
                ensure_owner_or_admin(current_user, chat_data.user_id)
            await db.refresh(session)
            ensure_owner_or_admin(current_user, session.user_id)
            if session.status == SessionStatus.CLOSED:
                raise HTTPException(status_code=400, detail="会话已关闭")
            user_message = Message(session_id=session_id, role=MessageRole.USER, content=chat_data.message)
            db.add(user_message)
            session.message_count += 1
            session.updated_at = datetime.utcnow()
            await db.commit()
            await assert_owner()
            agent_state = await chat_service.hydrate_state(session_id, chat_data.user_id, chat_data.message)
            agent_state.source_message_id = user_message.id
            agent_state.actor_id = current_user.id
            agent_state = await chat_service.process(agent_state)
            await assert_owner()
            assistant_message, _evaluation = await chat_service.persist_result(
                db, session, agent_state, chat_data.message, request_trace_id,
            )
            if session.message_count >= 10:
                recent = (await db.execute(select(Message).where(Message.session_id == session_id).order_by(Message.id.desc()).limit(12))).scalars().all()
                session.summary = _build_rule_summary(list(reversed(recent)), agent_state.detected_intent)
                await db.commit()
    except SessionBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SessionLeaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ChatResponse(
        message_id=assistant_message.id,
        session_id=session_id,
        reply=agent_state.reply,
        intent=agent_state.detected_intent,
        response_time_ms=agent_state.execution_time_ms,
        trace_id=request_trace_id,
    )


@router.get("/history/{session_id}", response_model=APIResponse)
async def get_chat_history(
    session_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await db.get(Session, session_id)
    if not session:
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
    """WebSocket 实时对话流式接口"""
    auth_header = websocket.headers.get("authorization", "")
    scheme, _, credential = auth_header.partition(" ")
    token = credential.strip() if scheme.lower() == "bearer" else None
    token = token or websocket.query_params.get("access_token")
    if not token:
        await websocket.close(code=4401, reason="请先登录")
        return
    from app.utils.database import async_session
    try:
        async with async_session() as auth_db:
            authenticated_user_id = (await authenticate_access_token(token, auth_db)).id
    except HTTPException:
        await websocket.close(code=4401, reason="Token 无效")
        return
    except SQLAlchemyError:
        await websocket.close(code=1013, reason="认证服务暂时不可用")
        return
    await websocket.accept()
    trace_id = generate_trace_id()
    structured_logger.log_request(
        trace_id=trace_id,
        method="WS",
        path="/api/v1/chat/stream",
        detail="WebSocket connected",
    )

    db = None
    try:
        while True:
            raw_data = await websocket.receive_text()
            # 每条消息都重新验证到期、撤销、禁用和数据库角色；握手身份不是长连接授权。
            try:
                async with async_session() as auth_db:
                    await authenticate_access_token(token, auth_db)
            except HTTPException as exc:
                await websocket.send_json({"type": "error", "code": 401, "message": exc.detail,
                                           "timestamp": datetime.now().isoformat()})
                await websocket.close(code=4401, reason="身份已失效")
                return
            except SQLAlchemyError:
                await websocket.send_json({"type": "error", "code": 503, "message": "认证服务暂时不可用",
                                           "timestamp": datetime.now().isoformat()})
                await websocket.close(code=1013, reason="认证服务暂时不可用")
                return
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "code": 400, "message": "JSON 格式错误", "timestamp": datetime.now().isoformat()}
                )
                continue

            user_id = data.get("user_id")
            message = data.get("message", "").strip()
            session_id = data.get("session_id")

            if user_id and int(user_id) != authenticated_user_id:
                await websocket.send_json({"type": "error", "code": 403, "message": "无权使用其他用户身份", "timestamp": datetime.now().isoformat()})
                continue
            user_id = authenticated_user_id
            enforce_rate_limit(f"chat:{authenticated_user_id}", settings.CHAT_RATE_LIMIT_PER_MINUTE)

            if not user_id:
                await websocket.send_json(
                    {"type": "error", "code": 400, "message": "缺少 user_id", "timestamp": datetime.now().isoformat()}
                )
                continue

            if not message:
                await websocket.send_json(
                    {"type": "error", "code": 400, "message": "消息内容不能为空", "timestamp": datetime.now().isoformat()}
                )
                continue
            if len(message) > settings.CHAT_MESSAGE_MAX_LENGTH:
                await websocket.send_json({"type": "error", "code": 413, "message": "消息长度超出限制", "timestamp": datetime.now().isoformat()})
                continue

            async with async_session() as db:
                if session_id:
                    session = await db.get(Session, session_id)
                    if not session:
                        await websocket.send_json({"type": "error", "code": 404, "message": "会话不存在", "timestamp": datetime.now().isoformat()})
                        continue
                    elif session.user_id != authenticated_user_id:
                        await websocket.send_json({"type": "error", "code": 403, "message": "无权访问该会话", "timestamp": datetime.now().isoformat()})
                        continue
                else:
                    session_id = str(uuid.uuid4())
                    session = Session(id=session_id, user_id=user_id, status=SessionStatus.ACTIVE)
                    db.add(session)

                await db.commit()
                try:
                    async with session_manager.session_lock(session_id) as assert_owner:
                        # 等锁期间可能已注销；前面已结束读事务，避免沿用旧SQL快照。
                        await authenticate_access_token(token, db)
                        await db.refresh(session)
                        if session.user_id != authenticated_user_id:
                            raise HTTPException(status_code=403, detail="无权访问该会话")
                        if session.status == SessionStatus.CLOSED:
                            raise HTTPException(status_code=400, detail="会话已关闭")
                        user_message = Message(
                            session_id=session_id,
                            role=MessageRole.USER,
                            content=message,
                        )
                        db.add(user_message)
                        session.message_count += 1
                        await db.commit()

                        await assert_owner()
                        agent_state = await chat_service.hydrate_state(session_id, user_id, message)
                        agent_state.source_message_id = user_message.id
                        agent_state.actor_id = authenticated_user_id

                        structured_logger.log_agent(
                            trace_id=trace_id,
                            node="stream",
                            intent="stream_start",
                            session_id=session_id,
                            detail=f"user_id={user_id}, message={message[:50]}",
                        )

                        await websocket.send_json({
                            "type": "stream_start",
                            "session_id": session_id,
                            "trace_id": trace_id,
                            "timestamp": datetime.now().isoformat(),
                        })

                        full_reply = ""
                        stream_agen = chat_service.process_stream(agent_state)
                        try:
                            async for event in stream_agen:
                                if event["type"] == "token":
                                    full_reply += event["content"]
                                elif event["type"] == "done":
                                    full_reply = event.get("reply", full_reply)

                                await websocket.send_json(event)
                        finally:
                            # 断开/异常时显式关闭生成器，触发 run_stream 取消 producer，
                            # 终止仍在进行的 LLM 生成，避免空烧调用与多余日志。
                            await stream_agen.aclose()

                        agent_state.reply = agent_state.reply or full_reply
                        await assert_owner()
                        assistant_message, stream_evaluation = await chat_service.persist_result(
                            db, session, agent_state, message, trace_id
                        )

                        get_tracer().record_response(agent_state.execution_time_ms, True)

                        await websocket.send_json({
                            "type": "stream_end",
                            "message_id": assistant_message.id,
                            "session_id": session_id,
                            "timestamp": datetime.now().isoformat(),
                        })

                        structured_logger.log_agent(
                            trace_id=trace_id,
                            node="stream",
                            intent="stream_end",
                            session_id=session_id,
                            detail=f"intent={agent_state.detected_intent}, time={agent_state.execution_time_ms}ms",
                        )

                except HTTPException as exc:
                    await db.rollback()
                    await websocket.send_json({"type": "error", "code": exc.status_code,
                                               "message": exc.detail, "timestamp": datetime.now().isoformat()})
                    if exc.status_code == 401:
                        await websocket.close(code=4401, reason="身份已失效")
                        return
                    continue
                except (SessionBusyError, SessionLeaseError) as exc:
                    await db.rollback()
                    await websocket.send_json({
                        "type": "error", "code": 409 if isinstance(exc, SessionBusyError) else 503,
                        "message": str(exc), "timestamp": datetime.now().isoformat(),
                    })
                    continue

    except WebSocketDisconnect:
        structured_logger.log_agent(
            trace_id=trace_id,
            node="stream",
            intent="ws_disconnect",
            session_id="unknown",
            detail="Client disconnected",
        )
    except Exception as e:
        structured_logger.log_error(
            trace_id=trace_id,
            error_type="ws_error",
            error_message=type(e).__name__,
            method="WS",
            path="/api/v1/chat/stream",
        )
        try:
            await websocket.send_json({
                "type": "error",
                "code": 500,
                "message": "服务器内部错误，请稍后重试",
                "timestamp": datetime.now().isoformat(),
            })
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
