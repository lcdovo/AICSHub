import json
import time
import uuid
import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.utils.database import get_db
from app.models.models import (
    Session, SessionStatus, Message, MessageRole, User, AgentTrace, EvaluationResult
)
from app.schemas.schemas import ChatRequest, ChatResponse, APIResponse
from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.agent.memory import session_manager
from app.utils.tracking import structured_logger, generate_trace_id, get_tracer
from app.security import get_current_user, ensure_owner_or_admin, enforce_rate_limit, require_user, decode_token
from app.config.config import settings
from app.services.evaluation import get_answer_evaluator, get_low_score_pool

router = APIRouter(prefix="/api/v1/chat", tags=["对话管理"])

agent_graph = AgentGraph()

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
    ensure_owner_or_admin(current_user, user_id)
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    session_id = str(uuid.uuid4())
    session = Session(
        id=session_id,
        user_id=user_id,
        status=SessionStatus.ACTIVE,
    )
    db.add(session)
    await db.commit()

    return APIResponse(
        code=0,
        message="会话创建成功",
        data={
            "id": session_id,
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
        if session.status == SessionStatus.CLOSED:
            raise HTTPException(status_code=400, detail="会话已关闭")
        ensure_owner_or_admin(current_user, session.user_id)
    else:
        session_id = str(uuid.uuid4())
        session = Session(
            id=session_id,
            user_id=chat_data.user_id,
            status=SessionStatus.ACTIVE,
        )
        db.add(session)
        await db.commit()

    # 保存用户消息
    user_message = Message(
        session_id=session_id,
        role=MessageRole.USER,
        content=chat_data.message,
    )
    db.add(user_message)
    session.message_count += 1
    session.updated_at = datetime.utcnow()
    await db.commit()

    # 使用 Agent 处理
    agent_state = AgentState(
        session_id=session_id,
        user_id=chat_data.user_id,
        user_message=chat_data.message,
    )

    # 从会话管理器加载历史上下文
    saved_state = await session_manager.get_state(session_id)
    if saved_state:
        agent_state.messages = saved_state.messages
        agent_state.collected_info = saved_state.collected_info

    # 运行 Agent 状态机
    agent_state = await agent_graph.run(agent_state)

    # 保存 Agent 执行轨迹
    llm_governance = agent_state.collected_info.get("llm_governance", {})
    if agent_state.trace:
        for i, trace_entry in enumerate(agent_state.trace):
            span_id = str(uuid.uuid4())
            agent_trace = AgentTrace(
                # trace_id 保持历史唯一约束；request_trace_id 是一次请求复用的规范 Trace ID。
                trace_id=f"{request_trace_id}:{span_id}",
                request_trace_id=request_trace_id,
                span_id=span_id,
                session_id=session_id,
                intent=agent_state.detected_intent,
                node_name=trace_entry.get("node", "unknown"),
                node_order=i,
                input_data=trace_entry.get("input"),
                output_data=trace_entry.get("output"),
                duration_ms=trace_entry.get("duration_ms", 0),
                token_usage={"total_tokens": agent_state.total_tokens},
                model_name=llm_governance.get("model", "mock"),
                prompt_version=llm_governance.get("prompt_version", settings.LLM_PROMPT_VERSION),
                schema_version=llm_governance.get("schema_version", settings.LLM_SCHEMA_VERSION),
                route_version=llm_governance.get("route_version", settings.LLM_ROUTE_VERSION),
                success=True,
            )
            db.add(agent_trace)

    # 保存助手回复
    response_time_ms = agent_state.execution_time_ms
    assistant_message = Message(
        session_id=session_id,
        role=MessageRole.ASSISTANT,
        content=agent_state.reply,
        token_count=agent_state.total_tokens,
        response_time_ms=response_time_ms,
        tool_calls=[{"name": tc.tool_name, "success": tc.success} for tc in agent_state.tool_calls],
    )
    db.add(assistant_message)
    await db.flush()

    # 评测结果与对话在同一事务中落库，避免只存在进程内存。
    evaluation = get_answer_evaluator().evaluate(
        response=agent_state.reply or "", user_query=chat_data.message,
        tool_results=[{"success": tc.success, "data": tc.result} for tc in agent_state.tool_calls],
        response_time_ms=response_time_ms,
    )
    get_answer_evaluator().add_feedback_sample({
        "sample_id": str(assistant_message.id), "trace_id": request_trace_id,
        "query": chat_data.message, "response": agent_state.reply or "", "score": evaluation.to_dict(),
    })
    db.add(EvaluationResult(
        session_id=session_id, sample_id=str(assistant_message.id), trace_id=request_trace_id, intent=agent_state.detected_intent,
        accuracy_score=round(evaluation.accuracy_score), completeness_score=round(evaluation.completeness_score),
        safety_score=round(evaluation.safety_score), overall_score=round(evaluation.overall_score),
        is_low_score=evaluation.is_low_score, failure_reason=evaluation.failure_reason,
        evaluator="heuristic", model_name=llm_governance.get("model", "heuristic"), prompt_version=llm_governance.get("prompt_version", settings.LLM_PROMPT_VERSION), schema_version=llm_governance.get("schema_version", settings.LLM_SCHEMA_VERSION), route_version=llm_governance.get("route_version", settings.LLM_ROUTE_VERSION),
        latency_ms=response_time_ms, token_usage={"total_tokens": agent_state.total_tokens},
        user_query=chat_data.message, response=agent_state.reply or "", categories=[],
        processed=False,
    ))
    get_tracer()._metrics.record_intent(agent_state.detected_intent, agent_state.intent_confidence)
    get_tracer()._metrics.record_evaluation(evaluation.overall_score, evaluation.is_low_score)
    for tool_call in agent_state.tool_calls:
        get_tracer()._metrics.record_tool_call(tool_call.tool_name, tool_call.success, getattr(tool_call, "execution_time_ms", 0))
        if tool_call.tool_name == "hybrid_search" and isinstance(tool_call.result, dict):
            rag_meta = tool_call.result.get("meta", {})
            get_tracer()._metrics.record_rag(
                latency_ms=tool_call.result.get("execution_time_ms", 0),
                empty=not bool(tool_call.result.get("results")),
                backend=rag_meta.get("backend", "memory"),
                degraded=bool(rag_meta.get("degraded", False)),
            )
    if agent_state.need_human:
        get_tracer()._metrics.record_handoff()
    get_low_score_pool().add_sample(
        sample_id=str(assistant_message.id), session_id=session_id,
        user_query=chat_data.message, response=agent_state.reply or "",
        score=evaluation.overall_score, failure_reason=evaluation.failure_reason,
        metadata={"trace_id": request_trace_id},
    ) if evaluation.is_low_score else None

    session.message_count += 1
    session.last_intent = agent_state.detected_intent
    session.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(assistant_message)

    if session.message_count >= 10:
        recent = (await db.execute(select(Message).where(Message.session_id == session_id).order_by(Message.id.desc()).limit(12))).scalars().all()
        session.summary = _build_rule_summary(list(reversed(recent)), agent_state.detected_intent)
        await db.commit()

    # 更新会话管理器状态
    await session_manager.save_state(agent_state)

    return ChatResponse(
        message_id=assistant_message.id,
        session_id=session_id,
        reply=agent_state.reply,
        intent=agent_state.detected_intent,
        response_time_ms=response_time_ms,
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
    token = auth_header.removeprefix("Bearer ").strip() or websocket.query_params.get("access_token")
    if not token:
        await websocket.close(code=4401, reason="请先登录")
        return
    try:
        payload = decode_token(token, "access")
        authenticated_user_id = int(payload["sub"])
    except (HTTPException, KeyError, TypeError, ValueError):
        await websocket.close(code=4401, reason="Token 无效")
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

            from app.utils.database import async_session
            async with async_session() as db:
                user = await db.get(User, user_id)
                if not user:
                    await websocket.send_json(
                        {"type": "error", "code": 404, "message": "用户不存在", "timestamp": datetime.now().isoformat()}
                    )
                    continue

                if session_id:
                    session = await db.get(Session, session_id)
                    if not session:
                        await websocket.send_json({"type": "error", "code": 404, "message": "会话不存在", "timestamp": datetime.now().isoformat()})
                        continue
                    elif session.user_id != authenticated_user_id:
                        await websocket.send_json({"type": "error", "code": 403, "message": "无权访问该会话", "timestamp": datetime.now().isoformat()})
                        continue
                    elif session.status == SessionStatus.CLOSED:
                        session.status = SessionStatus.ACTIVE
                        session.closed_at = None
                else:
                    session_id = str(uuid.uuid4())
                    session = Session(id=session_id, user_id=user_id, status=SessionStatus.ACTIVE)
                    db.add(session)

                user_message = Message(
                    session_id=session_id,
                    role=MessageRole.USER,
                    content=message,
                )
                db.add(user_message)
                await db.commit()

                agent_state = AgentState(
                    session_id=session_id,
                    user_id=user_id,
                    user_message=message,
                )

                saved_state = await session_manager.get_state(session_id)
                if saved_state:
                    agent_state.messages = saved_state.messages
                    agent_state.collected_info = saved_state.collected_info

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
                async for event in agent_graph.run_stream(agent_state):
                    if event["type"] == "token":
                        full_reply += event["content"]
                    elif event["type"] == "done":
                        full_reply = event.get("reply", full_reply)

                    await websocket.send_json(event)

                assistant_message = Message(
                    session_id=session_id,
                    role=MessageRole.ASSISTANT,
                    content=agent_state.reply or full_reply,
                    token_count=agent_state.total_tokens,
                    response_time_ms=agent_state.execution_time_ms,
                    tool_calls=[{"name": tc.tool_name, "success": tc.success} for tc in agent_state.tool_calls],
                )
                db.add(assistant_message)
                await db.flush()

                stream_evaluation = get_answer_evaluator().evaluate(
                    response=agent_state.reply or full_reply, user_query=message,
                    tool_results=[{"success": tc.success, "data": tc.result} for tc in agent_state.tool_calls],
                    response_time_ms=agent_state.execution_time_ms,
                )
                get_answer_evaluator().add_feedback_sample({
                    "sample_id": str(assistant_message.id), "trace_id": trace_id,
                    "query": message, "response": agent_state.reply or full_reply,
                    "score": stream_evaluation.to_dict(),
                })
                db.add(EvaluationResult(
                    session_id=session_id, sample_id=str(assistant_message.id), trace_id=trace_id, intent=agent_state.detected_intent,
                    accuracy_score=round(stream_evaluation.accuracy_score), completeness_score=round(stream_evaluation.completeness_score),
                    safety_score=round(stream_evaluation.safety_score), overall_score=round(stream_evaluation.overall_score),
                    is_low_score=stream_evaluation.is_low_score, failure_reason=stream_evaluation.failure_reason,
                    evaluator="heuristic", model_name=agent_state.collected_info.get("llm_governance", {}).get("model", "mock"),
                    prompt_version=agent_state.collected_info.get("llm_governance", {}).get("prompt_version", settings.LLM_PROMPT_VERSION),
                    schema_version=agent_state.collected_info.get("llm_governance", {}).get("schema_version", settings.LLM_SCHEMA_VERSION),
                    route_version=agent_state.collected_info.get("llm_governance", {}).get("route_version", settings.LLM_ROUTE_VERSION), latency_ms=agent_state.execution_time_ms,
                    token_usage={"total_tokens": agent_state.total_tokens}, user_query=message,
                    response=agent_state.reply or full_reply, categories=[], processed=False,
                ))

                if agent_state.trace:
                    for i, trace_entry in enumerate(agent_state.trace):
                        span_id = str(uuid.uuid4())
                        agent_trace = AgentTrace(
                            trace_id=f"{trace_id}:{span_id}",
                            request_trace_id=trace_id,
                            span_id=span_id,
                            session_id=session_id,
                            intent=agent_state.detected_intent,
                            node_name=trace_entry.get("node", "unknown"),
                            node_order=i,
                            input_data=trace_entry.get("input"),
                            output_data=trace_entry.get("output"),
                            duration_ms=trace_entry.get("duration_ms", 0),
                            token_usage={"total_tokens": agent_state.total_tokens},
                            model_name=agent_state.collected_info.get("llm_governance", {}).get("model", "mock"),
                            prompt_version=agent_state.collected_info.get("llm_governance", {}).get("prompt_version", settings.LLM_PROMPT_VERSION),
                            schema_version=agent_state.collected_info.get("llm_governance", {}).get("schema_version", settings.LLM_SCHEMA_VERSION),
                            route_version=agent_state.collected_info.get("llm_governance", {}).get("route_version", settings.LLM_ROUTE_VERSION),
                            success=True,
                        )
                        db.add(agent_trace)

                session.message_count += 2
                session.last_intent = agent_state.detected_intent
                session.updated_at = datetime.utcnow()
                await db.commit()
                await db.refresh(assistant_message)

                await session_manager.save_state(agent_state)
                tracer = get_tracer()
                tracer._metrics.record_intent(agent_state.detected_intent, agent_state.intent_confidence)
                tracer._metrics.record_evaluation(stream_evaluation.overall_score, stream_evaluation.is_low_score)
                if stream_evaluation.is_low_score:
                    get_low_score_pool().add_sample(
                        sample_id=str(assistant_message.id), session_id=session_id,
                        user_query=message, response=agent_state.reply or full_reply,
                        score=stream_evaluation.overall_score,
                        failure_reason=stream_evaluation.failure_reason,
                        metadata={"trace_id": trace_id},
                    )
                for tool_call in agent_state.tool_calls:
                    tracer._metrics.record_tool_call(tool_call.tool_name, tool_call.success)
                tracer._metrics.record_response(agent_state.execution_time_ms, True)

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
            error_message=str(e),
            method="WS",
            path="/api/v1/chat/stream",
        )
        try:
            await websocket.send_json({
                "type": "error",
                "code": 500,
                "message": f"服务器内部错误: {str(e)}",
                "timestamp": datetime.now().isoformat(),
            })
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
