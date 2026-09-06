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
from app.agent.memory import session_manager
from app.agent.state import AgentState
from app.config.config import settings
from app.models.models import AgentTrace, EvaluationResult, Message, MessageRole, Session
from app.services.evaluation import get_answer_evaluator, get_low_score_pool
from app.utils.tracking import get_tracer


class ChatService:
    """Application service shared by REST and WebSocket chat transports."""

    def __init__(self, agent_graph: Optional[AgentGraph] = None):
        self.agent_graph = agent_graph

    async def hydrate_state(self, session_id: str, user_id: int, message: str) -> AgentState:
        state = AgentState(session_id=session_id, user_id=user_id, user_message=message)
        saved = await session_manager.get_state(session_id)
        if saved:
            state.messages = saved.messages
            state.collected_info = saved.collected_info
            state.detected_intent = saved.detected_intent
            state.intent_confidence = saved.intent_confidence
            state.pending_confirmations = saved.pending_confirmations
            state.conversation_summary = saved.conversation_summary
        # 认证入口传入的用户与当前会话才是可信身份，不能沿用缓存状态中的值。
        state.collected_info["user_id"] = user_id
        state.collected_info["session_id"] = session_id
        state.messages.append({"role": "user", "content": message, "timestamp": datetime.now().isoformat()})
        return state

    async def process(self, state: AgentState) -> AgentState:
        if state.collected_info.get("graph_interrupt"):
            return await self.agent_graph.resume(state.session_id, state, state.user_message)
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
    ) -> tuple[Message, Any]:
        """Persist the assistant result and common evaluation/trace records."""
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
        get_answer_evaluator().add_feedback_sample({
            "sample_id": str(assistant_message.id),
            "trace_id": trace_id,
            "query": user_query,
            "response": state.reply or "",
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
            user_query=user_query,
            response=state.reply or "",
            categories=[],
            processed=False,
        ))

        tracer = get_tracer()
        tracer._metrics.record_intent(state.detected_intent, state.intent_confidence)
        tracer._metrics.record_evaluation(evaluation.overall_score, evaluation.is_low_score)
        if state.need_human:
            tracer._metrics.record_handoff()
        if evaluation.is_low_score:
            get_low_score_pool().add_sample(
                sample_id=str(assistant_message.id),
                session_id=session.id,
                user_query=user_query,
                response=state.reply or "",
                score=evaluation.overall_score,
                failure_reason=evaluation.failure_reason,
                metadata={"trace_id": trace_id},
            )
        for tool_call in state.tool_calls:
            tracer._metrics.record_tool_call(tool_call.tool_name, tool_call.success)
            if tool_call.tool_name == "hybrid_search" and isinstance(tool_call.result, dict):
                meta = tool_call.result.get("meta", {})
                tracer._metrics.record_rag(
                    latency_ms=tool_call.result.get("execution_time_ms", 0),
                    empty=not bool(tool_call.result.get("results")),
                    backend=meta.get("backend", "memory"),
                    degraded=bool(meta.get("degraded", False)),
                )

        for index, trace_entry in enumerate(state.trace):
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
        await db.commit()
        await db.refresh(assistant_message)
        await session_manager.save_state(state)
        return assistant_message, evaluation


chat_service = ChatService()
