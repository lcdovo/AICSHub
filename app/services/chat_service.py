"""Shared chat orchestration for synchronous and streaming endpoints.

The HTTP and WebSocket adapters should differ only in transport.  This module
keeps AgentState hydration and graph execution in one place; the streaming
adapter also uses the shared persistence/evaluation finalizer.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import AgentGraph
from app.agent.memory import session_manager, SessionLeaseError
from app.agent.state import AgentState
from app.config.config import settings
from app.models.models import AgentTrace, EvaluationResult, Message, MessageRole, Session
from app.services.evaluation import get_answer_evaluator, get_low_score_pool
from app.utils.tracking import get_tracer


class ChatService:
    """Application service shared by REST and WebSocket chat transports."""

    def __init__(self, agent_graph: Optional[AgentGraph] = None):
        self.agent_graph = agent_graph

    async def hydrate_state(self, session_id: str, user_id: int, message: str, *, action_id: str | None = None,
                            recover_from_db: bool = False) -> AgentState:
        try:
            saved = await session_manager.get_state(session_id)
        except SessionLeaseError:
            if not recover_from_db:
                raise
            saved = None
        if saved is not None and (saved.session_id != session_id or saved.user_id != user_id):
            if not recover_from_db:
                raise SessionLeaseError("共享会话状态归属不一致，请联系管理员核查")
            saved = None
        source = saved or AgentState(session_id=session_id, user_id=user_id, user_message="")
        state = source.for_next_turn(user_id, message)
        # 动作及历史以数据库恢复，Redis快照缺失不能遗忘已提交申请或待确认预览。
        from app.models.models import RefundAction
        from app.services.refund_actions import action_receipt
        from app.utils.database import async_session
        from sqlalchemy import select
        if recover_from_db:
            async with async_session() as db:
                persisted = await db.get(Session, session_id)
                if persisted is None or persisted.deleted_at is not None:
                    raise SessionLeaseError("会话已删除或不存在")
                if persisted.user_id != user_id:
                    raise SessionLeaseError("会话归属不匹配")
                state.session_version = persisted.version
                projection = persisted.context_data or {}
                state.collected_info = dict(projection.get("collected_info") or {})
                state.pending_confirmations = list(projection.get("pending_confirmations") or [])
                state.missing_slots = list(projection.get("missing_slots") or [])
                state.needs_clarification = bool(projection.get("needs_clarification"))
                # 会话投影/checkpoint只保存恢复提示，不能把旧确认或终态动作复活。
                projected_action = bool(
                    state.collected_info.get("refund_preview")
                    or state.collected_info.get("refund_action")
                    or "apply_refund" in state.pending_confirmations
                )
                for field in ("refund_preview", "refund_action", "confirm_refund"):
                    state.collected_info.pop(field, None)
                state.pending_confirmations = [
                    item for item in state.pending_confirmations if item != "apply_refund"
                ]
                if projected_action:
                    for field in ("reason", "refund_type"):
                        state.collected_info.pop(field, None)
                state.conversation_summary = persisted.summary
                history = (await db.execute(select(Message).where(Message.session_id == session_id, Message.id > persisted.summary_cursor)
                                            .order_by(Message.id.desc()).limit(40))).scalars().all()
                transcript = [{"role": m.role.value, "content": m.content} for m in reversed(history)]
                if transcript and transcript[-1] == {"role": "user", "content": message}:
                    transcript.pop()
                state.messages = transcript + [{"role": "user", "content": message}]
                query = select(RefundAction).where(RefundAction.session_id == session_id, RefundAction.user_id == user_id)
                query = query.where(RefundAction.id == action_id) if action_id else query.where(
                    RefundAction.status == "pending", RefundAction.expires_at > datetime.utcnow()
                )
                action = await db.scalar(query.order_by(RefundAction.created_at.desc()).limit(1))
                if action:
                    if action.status == "pending" and action.expires_at > datetime.utcnow():
                        state.pending_confirmations.append("apply_refund")
                    state.collected_info.update(order_id=action.order_id, reason=action.reason, refund_type=action.type,
                        refund_action=action_receipt(action), refund_preview={"order_id": action.order_id,
                        "reason": action.reason, "type": action.type, "user_id": user_id, "session_id": session_id,
                        "action_id": action.id, "action_version": action.version, "idempotency_key": f"refund-action:{action.id}"})
        if saved and not recover_from_db:
            summary = await session_manager.get_summary(session_id)
            if summary and summary.intent_history and not state.collected_info.get("intent_history"):
                state.collected_info["intent_history"] = list(summary.intent_history)
        from app.agent.request_understanding import ConversationContext
        state.conversation_context=ConversationContext(messages=state.messages[-6:],known_order_id=state.collected_info.get("order_id"),pending_action_id=(state.collected_info.get("refund_preview",{}).get("action_id") if "apply_refund" in state.pending_confirmations else None))
        return state

    async def process(self, state: AgentState) -> AgentState:
        # Both transports start from the clean turn state. An unfinished preview is
        # re-evaluated by confirmation_node; never resurrect a previous turn's draft.
        return await self.agent_graph.run(state)

    async def process_stream(self, state: AgentState):
        async for event in self.agent_graph.run_stream(state):
            yield event

    async def persist_result(
        self,
        db: AsyncSession,
        session: Session,
        state: AgentState,
        user_query: str,
        trace_id: str,
        *,
        assistant_message: Optional[Message] = None,
        commit: bool = True,
    ) -> tuple[Message, Any]:
        """Persist the assistant result and common evaluation/trace records."""
        # An interrupt returns its preview before response_generation can append it.
        # Keep the persisted transcript and next-turn history consistent.
        if not state.messages or state.messages[-1].get("role") != "assistant" or state.messages[-1].get("content") != state.reply:
            state.messages.append({"role": "assistant", "content": state.reply,
                                   "timestamp": datetime.now().isoformat(timespec="milliseconds")})
        if assistant_message is None:
            assistant_message = Message(
                session_id=session.id,
                role=MessageRole.ASSISTANT,
                content=state.reply,
                token_count=state.total_tokens,
                response_time_ms=state.execution_time_ms,
                tool_calls=[{"name": tc.tool_name, "success": tc.success} for tc in state.tool_calls],
            )
            db.add(assistant_message)
            await db.flush()

        governance = state.collected_info.get("llm_governance", {})
        evaluation = get_answer_evaluator().evaluate(
            response=state.reply or "",
            user_query=user_query,
            tool_results=[{"success": tc.success, "data": tc.result} for tc in state.tool_calls],
            response_time_ms=state.execution_time_ms,
        )
        from app.services.llm_governance import SensitiveDataRedactor
        redactor = SensitiveDataRedactor()
        report_query, report_response = redactor.redact(user_query), redactor.redact(state.reply or "")
        get_answer_evaluator().add_feedback_sample({
            "sample_id": str(assistant_message.id),
            "trace_id": trace_id,
            "query": report_query,
            "response": report_response,
            "score": evaluation.to_dict(),
        })
        db.add(EvaluationResult(
            session_id=session.id,
            sample_id=str(assistant_message.id),
            trace_id=trace_id,
            intent=state.detected_intent,
            accuracy_score=round(evaluation.accuracy_score),
            completeness_score=round(evaluation.completeness_score),
            safety_score=round(evaluation.safety_score),
            overall_score=round(evaluation.overall_score),
            is_low_score=evaluation.is_low_score,
            failure_reason=evaluation.failure_reason,
            evaluator="heuristic",
            model_name=governance.get("model", "heuristic"),
            prompt_version=governance.get("prompt_version", settings.LLM_PROMPT_VERSION),
            schema_version=governance.get("schema_version", settings.LLM_SCHEMA_VERSION),
            route_version=governance.get("route_version", settings.LLM_ROUTE_VERSION),
            latency_ms=state.execution_time_ms,
            token_usage={"total_tokens": state.total_tokens},
            user_query=report_query,
            response=report_response,
            categories=[],
            processed=False,
        ))

        tracer = get_tracer()
        tracer.record_intent(state.detected_intent, state.intent_confidence)
        tracer.record_evaluation(evaluation.overall_score, evaluation.is_low_score)
        if state.need_human:
            tracer.record_handoff()
        if evaluation.is_low_score:
            get_low_score_pool().add_sample(
                sample_id=str(assistant_message.id),
                session_id=session.id,
                user_query=report_query,
                response=report_response,
                score=evaluation.overall_score,
                failure_reason=evaluation.failure_reason,
                metadata={"trace_id": trace_id},
            )
        for tool_call in state.tool_calls:
            tracer.record_tool_call(tool_call.tool_name, tool_call.success)
            if tool_call.tool_name == "hybrid_search" and isinstance(tool_call.result, dict):
                meta = tool_call.result.get("meta", {})
                tracer.record_rag(
                    latency_ms=tool_call.result.get("execution_time_ms", 0),
                    empty=not bool(tool_call.result.get("results")),
                    backend=meta.get("backend", "memory"),
                    degraded=bool(meta.get("degraded", False)),
                )

        for index, trace_entry in enumerate(state.trace):
            from app.services.llm_governance import SensitiveDataRedactor
            trace_entry = SensitiveDataRedactor().redact_value(trace_entry)
            span_id = str(uuid.uuid4())
            db.add(AgentTrace(
                trace_id=f"{trace_id}:{span_id}",
                request_trace_id=trace_id,
                span_id=span_id,
                session_id=session.id,
                intent=state.detected_intent,
                node_name=trace_entry.get("node", "unknown"),
                node_order=index,
                input_data=trace_entry.get("input"),
                output_data=trace_entry.get("output"),
                duration_ms=trace_entry.get("duration_ms", 0),
                token_usage={"total_tokens": state.total_tokens},
                model_name=governance.get("model", "mock"),
                prompt_version=governance.get("prompt_version", settings.LLM_PROMPT_VERSION),
                schema_version=governance.get("schema_version", settings.LLM_SCHEMA_VERSION),
                route_version=governance.get("route_version", settings.LLM_ROUTE_VERSION),
                success=True,
            ))

        session.message_count += 1
        session.last_intent = state.detected_intent
        session.updated_at = datetime.utcnow()
        if not commit:
            return assistant_message, evaluation
        await db.commit()
        await db.refresh(assistant_message)
        await session_manager.save_state(state)
        # 长对话上下文压缩：消息数或 Token 估算超阈值时触发（LLM 摘要，降级规则摘要）。
        await session_manager.maybe_compress(state.session_id)
        # 如果有摘要，更新意图序列到摘要并持久化
        summary = await session_manager.get_summary(state.session_id)
        if summary:
            current_intent = state.detected_intent
            if current_intent not in summary.intent_history:
                summary.intent_history.append(current_intent)
                summary.intent_history = summary.intent_history[-10:]
            await session_manager._save_summary_to_redis(state.session_id, summary)
        return assistant_message, evaluation


chat_service = ChatService()
