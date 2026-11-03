"""
Agent 编排 - 基于官方 LangGraph 状态机的 13 节点业务编排（Phase 3 增强版）

图的骨架（StateGraph / interrupt / checkpointer）由官方 LangGraph 提供；
本模块只保留图的装配与路由：LangGraph 编译、节点注册、run/run_stream/resume
执行入口、意图→节点路由表，以及观察（Langfuse）注入。

13 个节点的业务编排实现已拆分到 app/agent/nodes/ 包（按域划分意图识别、RAG、
工具执行、响应生成、人工转接等），并在本类底部通过类属性绑定引用，保持
``self._node_xxx()`` 的既有调用契约不变，避免扩散修改调用方。
"""
import time
import asyncio
import contextlib
import contextvars
import logging
import random
from typing import AsyncGenerator, Dict, List, Optional, Any, Callable, TypedDict
from datetime import datetime

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END as LANGGRAPH_END
from langgraph.graph import StateGraph

from app.agent.state import AgentState, AgentNode, IntentType
from app.agent.redis_checkpoint import RedisCheckpointSaver
from app.agent.intent import EnhancedIntentRecognizer
from app.services.llm_governance import stable_gray_assignment
from app.agent.validation import create_validator
from app.agent.query_rewrite import ConservativeQueryRewriter
from app.agent.query_rewrite_llm import LLMQueryRewriter
from app.agent.validation_llm import LLMRAGJudge
from app.services.llm_service import LLMService
from app.services.knowledge_base import get_knowledge_base
from app.config.config import settings
from app.utils.langfuse_tracing import get_langfuse_handler
from app.utils.redis_cache import RedisObjectCache

from .nodes.start_node import start_node, end_node
from .nodes.intent_node import (
    intent_recognition_node,
    route_decision_node,
    slot_filling_node,
    clarification_node,
    detect_intent_escalation,
    is_refund_policy_inquiry,
    is_complaint_policy_inquiry,
    missing_slots_for_intent,
)
from .nodes.rag_node import query_rewrite_node, rag_retrieval_node
from .nodes.tool_node import (
    tool_execution_node,
    confirmation_node,
    result_verification_node,
    refund_confirmed,
    refund_cancelled,
    extract_info_from_message,
    select_tools_for_intent,
)
from .nodes.response_node import (
    response_generation_node,
    add_rag_citations,
    clean_chunk,
    build_answer_cache_key,
    answer_cache_get,
    answer_cache_set,
    try_templated_tool_reply,
    build_order_reply_template,
    try_action_reply_template,
    build_refund_reply_template,
    build_ticket_status_reply_template,
    build_escalate_reply_template,
    rag_judge_applicable,
    tool_evidence_text,
    generate_response_with_llm,
    invoke_llm_service,
    build_llm_prompt,
    build_context_messages,
    is_smalltalk,
    get_default_reply,
    ANSWER_CACHE_TTL,
    ANSWER_CACHE_MAX,
    EXPLAIN_MARKERS,
    STRONG_EMOTION_MARKERS,
)
from .nodes.handoff_node import human_handoff_node

logger = logging.getLogger(__name__)

# run_stream and response_generation share a queue through ContextVar.
# Provider drafts stay private; the response node queues only validated final text.
_stream_event_queue: contextvars.ContextVar[Optional[asyncio.Queue]] = contextvars.ContextVar(
    "agent_stream_event_queue", default=None
)


class AgentGraph:
    """Agent 状态机 - 核心编排器 (Phase 3)"""

    # 融合后 final_score 的最低锚定分（区别于 settings.RAG_SIMILARITY_THRESHOLD
    # 的原始向量分阈值，两者口径不同，不能互相替换）。
    RAG_MIN_SCORE = 0.35

    # ---------- 节点实现绑定（app/agent/nodes/ 包） ----------
    # 13 个 LangGraph 节点的业务编排已拆分至 nodes 包；此处通过类属性绑定，
    # 让图装配（_register_nodes）与既有测试（_node_xxx 直调）的调用契约不变。
    _node_start = start_node
    _node_end = end_node
    _node_intent_recognition = intent_recognition_node
    _node_route_decision = route_decision_node
    _node_slot_filling = slot_filling_node
    _node_clarification = clarification_node
    _node_query_rewrite = query_rewrite_node
    _node_rag_retrieval = rag_retrieval_node
    _node_tool_execution = tool_execution_node
    _node_confirmation = confirmation_node
    _node_result_verification = result_verification_node
    _node_response_generation = response_generation_node
    _node_human_handoff = human_handoff_node

    # 节点私有助手（随节点迁移至 nodes 包，仍绑定到类供 self.xxx 调用）
    _detect_intent_escalation = detect_intent_escalation
    _is_refund_policy_inquiry = is_refund_policy_inquiry
    _is_complaint_policy_inquiry = is_complaint_policy_inquiry
    _missing_slots_for_intent = missing_slots_for_intent
    _refund_confirmed = refund_confirmed
    _refund_cancelled = staticmethod(refund_cancelled)
    _extract_info_from_message = extract_info_from_message
    _select_tools_for_intent = select_tools_for_intent
    _add_rag_citations = add_rag_citations
    _clean_chunk = staticmethod(clean_chunk)
    _build_answer_cache_key = build_answer_cache_key
    _answer_cache_get = answer_cache_get
    _answer_cache_set = answer_cache_set
    _try_templated_tool_reply = try_templated_tool_reply
    _build_order_reply_template = staticmethod(build_order_reply_template)
    _try_action_reply_template = try_action_reply_template
    _build_refund_reply_template = staticmethod(build_refund_reply_template)
    _build_ticket_status_reply_template = staticmethod(build_ticket_status_reply_template)
    _build_escalate_reply_template = staticmethod(build_escalate_reply_template)
    _rag_judge_applicable = rag_judge_applicable
    _tool_evidence_text = staticmethod(tool_evidence_text)
    _generate_response_with_llm = generate_response_with_llm
    _invoke_llm_service = invoke_llm_service
    _build_llm_prompt = build_llm_prompt
    _build_context_messages = build_context_messages
    _is_smalltalk = staticmethod(is_smalltalk)
    _get_default_reply = get_default_reply

    # ---------- 响应节点域常量（自 nodes.response_node 绑定） ----------
    _ANSWER_CACHE_TTL = ANSWER_CACHE_TTL
    _ANSWER_CACHE_MAX = ANSWER_CACHE_MAX
    _EXPLAIN_MARKERS = EXPLAIN_MARKERS
    _STRONG_EMOTION_MARKERS = STRONG_EMOTION_MARKERS

    def __init__(self):
        self.llm_service = LLMService()
        self.intent_recognizer = EnhancedIntentRecognizer()
        self.query_rewriter = ConservativeQueryRewriter()
        self.llm_rewriter = LLMQueryRewriter(self.llm_service)
        self.llm_judge = LLMRAGJudge(self.llm_service)

        self.validator = create_validator()
        self.nodes: Dict[str, Callable] = {}
        self._register_nodes()
        self.llm_runnable = RunnableLambda(self._invoke_llm_service)
        # 退款确认 interrupt/多轮恢复的 checkpoint 持久化到 Redis：
        # 进程重启后仍可按 thread_id 恢复中断状态；Redis 不可用时自动降级内存。
        self.checkpointer = RedisCheckpointSaver()
        self.langgraph = self._build_langgraph()
        # RAG 回答缓存：同意图+同改写后查询在 TTL 内直接复用上次 LLM 回答（延迟优化）。
        # Redis 优先（多 worker 共享）+ 内存降级，见 RedisObjectCache。
        self._answer_cache = RedisObjectCache(
            prefix="answer:", default_ttl=self._ANSWER_CACHE_TTL, max_local=self._ANSWER_CACHE_MAX
        )

    class _GraphState(TypedDict):
        state: AgentState
        next_node: str

    def _build_langgraph(self):
        """Compile the existing node workflow as a LangGraph state machine."""
        workflow = StateGraph(self._GraphState)
        for node_name in self.nodes:
            workflow.add_node(node_name, self._make_langgraph_node(node_name))
        workflow.set_entry_point(AgentNode.START)
        route_targets = {node_name: node_name for node_name in self.nodes}
        route_targets[AgentNode.END] = LANGGRAPH_END
        for node_name in self.nodes:
            if node_name == AgentNode.END:
                workflow.add_edge(node_name, LANGGRAPH_END)
            else:
                workflow.add_conditional_edges(
                    node_name,
                    lambda payload: payload["next_node"],
                    route_targets,
                )
        return workflow.compile(checkpointer=self.checkpointer)

    def _with_langfuse(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """按会话粒度注入 Langfuse 观测 handler（可配置开关 + 采样率）。

        采样按 session_id（thread_id）稳定哈希分桶：同一会话的所有轮次要么全采
        要么全不采，避免请求粒度 random 采样导致的轨迹断裂。关闭（默认）、未命中
        采样或 handler 初始化失败时返回原 config，保证外部观测平台不可用绝不
        影响主链路。
        """
        if not settings.LANGFUSE_ENABLED:
            return config
        session_id = (config.get("configurable") or {}).get("thread_id")
        percent = int(settings.LANGFUSE_SAMPLING_RATE * 100)
        if session_id:
            if not stable_gray_assignment(str(session_id), percent):
                return config
        elif random.random() >= settings.LANGFUSE_SAMPLING_RATE:
            return config
        handler = get_langfuse_handler()
        if handler is None:
            return config
        return {**config, "callbacks": [handler]}

    def _make_langgraph_node(self, node_name: str):
        async def invoke(payload: Dict[str, Any]) -> Dict[str, Any]:
            state = payload["state"]
            state.current_node = node_name
            state.node_steps += 1
            if state.node_steps>24:
                state.reply="本轮处理步骤已达上限，请稍后重试或联系人工。"
                return {"state":state,"next_node":AgentNode.END}
            node_start = time.time()
            next_node = await self.nodes[node_name](state)
            state.add_trace(
                node_name=node_name,
                input_data={"message": state.user_message, "intent": state.detected_intent},
                output_data={"next_node": next_node, "reply": state.reply[:100] if state.reply else ""},
                duration_ms=int((time.time() - node_start) * 1000),
            )
            if next_node == AgentNode.END:
                state.current_node = AgentNode.END
            return {"state": state, "next_node": next_node}
        return invoke

    def _register_nodes(self):
        from app.agent.nodes.rag_node import (
            QueryRewriteNode, QueryRewriteDependencies, RetrievalNode, RetrievalDependencies,
        )
        from app.agent.nodes.tool_node import (
            ToolExecutionNode, ToolExecutionDependencies, ConfirmationNode, ConfirmationDependencies,
            extract_info, select_tools, is_refund_confirmed, invoke_registered_tool,
            persistent_actions, verify_tool_result, interrupt, refund_cancelled,
        )
        from app.agent.nodes.intent_node import (
            IntentNode, IntentDependencies, RoutingNode, RoutingDependencies,
            ClarificationNode, ClarificationDependencies, fill_slots,
            detect_escalation, is_refund_inquiry, is_complaint_inquiry, required_slots, intent_destination,
        )
        self.nodes[AgentNode.START] = self._node_start
        self.nodes[AgentNode.INTENT_RECOGNITION] = IntentNode(IntentDependencies(
            self.intent_recognizer, lambda: self.llm_service, is_refund_confirmed, refund_cancelled,
            is_smalltalk, detect_escalation, persistent_actions(),
        ))
        self.nodes[AgentNode.ROUTE_DECISION] = RoutingNode(RoutingDependencies(
            extract_info, is_refund_inquiry, is_complaint_inquiry, required_slots, intent_destination,
        ))
        self.nodes[AgentNode.SLOT_FILLING] = fill_slots
        self.nodes[AgentNode.QUERY_REWRITE] = QueryRewriteNode(QueryRewriteDependencies(self.llm_rewriter))
        self.nodes[AgentNode.CLARIFICATION] = ClarificationNode(ClarificationDependencies(self.intent_recognizer))
        self.nodes[AgentNode.CONFIRMATION] = ConfirmationNode(ConfirmationDependencies(is_refund_confirmed, interrupt))
        self.nodes[AgentNode.RAG_RETRIEVAL] = RetrievalNode(RetrievalDependencies(
            knowledge_factory=lambda: get_knowledge_base(), top_k=lambda: settings.RAG_TOP_K,
            minimum_score=self.RAG_MIN_SCORE,
        ))
        self.nodes[AgentNode.TOOL_EXECUTION] = ToolExecutionNode(ToolExecutionDependencies(
            extract_info, select_tools, is_refund_confirmed, invoke_registered_tool, persistent_actions(),
        ))
        self.nodes[AgentNode.RESULT_VERIFICATION] = verify_tool_result
        self.nodes[AgentNode.RESPONSE_GENERATION] = self._node_response_generation
        self.nodes[AgentNode.HUMAN_HANDOFF] = self._node_human_handoff
        self.nodes[AgentNode.END] = self._node_end

    def _route_by_intent(self, intent: str) -> str:
        from app.agent.nodes.intent_node import intent_destination
        return intent_destination(intent)

    async def run_stream(self, state: AgentState) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.model_gateway import inherited_model_context

        async with inherited_model_context(state.user_id, "graph-stream", route_version=state.strategy_version):
            stream = self._run_stream(state)
            try:
                async for event in stream:
                    yield event
            finally:
                await stream.aclose()

    async def _run_stream(self, state: AgentState) -> AsyncGenerator[Dict[str, Any], None]:
        """以同一张 LangGraph 编排图执行流式请求。

        流式接口只负责把图的 updates 转换为事件，不再复制节点、路由或校验逻辑，
        从而保证 ``run`` 与 WebSocket 的副作用和终止条件一致。
        """
        start_time = time.time()
        previous_tool_count = len(state.tool_calls)
        initial_node = state.current_node
        # 保持既有 WebSocket 协议：首事件仍为入口节点。流式事实：
        # 节点级快照来自 LangGraph updates；文本在校验后由响应节点分片发布。
        yield {"type": "node_start", "node": initial_node, "timestamp": datetime.now().isoformat()}
        yield {"type": "stream_mode", "mode": "updates", "token_stream": "validated", "timestamp": datetime.now().isoformat()}
        first_update = True
        token_index = 0
        event_queue: asyncio.Queue = asyncio.Queue(maxsize=64)

        async def produce_updates():
            try:
                async for update in self.langgraph.astream(
                    {"state": state, "next_node": state.current_node},
                    config=self._with_langfuse(
                        {"recursion_limit": 50, "configurable": {"thread_id": state.session_id}}
                    ),
                    stream_mode="updates",
                ):
                    if update:
                        await event_queue.put(("update", update))
            except Exception as exc:
                await event_queue.put(("error", exc))
            finally:
                # 被取消且队列已满时不能在finally等待消费者，否则aclose会永久阻塞。
                if not asyncio.current_task().cancelling():
                    await event_queue.put(("graph_done", None))

        queue_token = _stream_event_queue.set(event_queue)
        producer = asyncio.create_task(produce_updates())
        # 本轮是否已向前端推送过 token 草稿：首次生成前的 token_reset 没有
        # 可清的草稿（纯协议噪声），跳过；仅当已推送过草稿（二次生成/重写）
        # 才转发 token_reset 让前端清空重打，最终稿仍由 done.reply 一次性定稿。
        emitted_token = False
        try:
          graph_done = False
          while not graph_done:
            kind, payload = await event_queue.get()
            if kind == "token_reset":
                # 回答被二次生成（校验重试）/LLM judge 拒答/附加引用改写时，
                # 新一轮 token 属于改写后的最终稿：让前端清空上一轮再重打，
                # 避免出现两版草稿文本拼接。
                if emitted_token:
                    yield {"type": "token_reset", "timestamp": datetime.now().isoformat()}
                continue
            if kind == "token":
                content = str(payload or "")
                if content:
                    emitted_token = True
                    yield {"type": "token", "content": content, "index": token_index,
                           "streaming": True, "provider": "validated",
                           "timestamp": datetime.now().isoformat()}
                    token_index += len(content)
                continue
            if kind == "error":
                raise payload
            if kind == "graph_done":
                graph_done = True
                continue
            update = payload
            if "__interrupt__" in update:
                state.response_ready = True
                continue
            node_name, payload = next(iter(update.items()))
            updated_state = payload.get("state", state)
            node_start = datetime.now().isoformat()
            if not (first_update and node_name == initial_node):
                yield {"type": "node_start", "node": node_name, "timestamp": node_start}
            first_update = False
            state = updated_state

            if node_name == AgentNode.INTENT_RECOGNITION:
                yield {"type": "intent", "intent": state.detected_intent, "confidence": state.intent_confidence,
                       "needs_clarification": state.needs_clarification, "timestamp": datetime.now().isoformat()}
            if node_name == AgentNode.QUERY_REWRITE:
                yield {"type": "query_rewrite", "applied": state.query_rewrite_applied,
                       "original_query": state.user_message,
                       "rewritten_query": state.rewritten_query or state.user_message,
                       "reason": state.query_rewrite_reason or "",
                       "timestamp": datetime.now().isoformat()}
            if node_name == AgentNode.RAG_RETRIEVAL:
                results = state.collected_info.get("kb_results", [])
                yield {"type": "rag_result", "results_count": len(results),
                       "top_score": results[0].get("final_score", 0) if results else 0,
                       "timestamp": datetime.now().isoformat()}
            if node_name == AgentNode.RESPONSE_GENERATION and state.collected_info.get("validation"):
                validation = state.collected_info["validation"]
                yield {"type": "validation", "passed": validation.get("passed", False),
                       "overall_score": validation.get("overall_score", 0),
                       "needs_regeneration": validation.get("needs_regeneration", False),
                       "timestamp": datetime.now().isoformat()}
            if node_name == AgentNode.HUMAN_HANDOFF:
                yield {"type": "handoff", "reason": state.human_reason or "需要人工介入",
                       "timestamp": datetime.now().isoformat()}

            for call in state.tool_calls[previous_tool_count:]:
                yield {"type": "tool_call_start", "tool": call.tool_name, "args": call.arguments,
                       "timestamp": datetime.now().isoformat()}
                yield {"type": "tool_call_complete", "tool": call.tool_name, "success": call.success,
                       "error": call.error_message, "timestamp": datetime.now().isoformat()}
            previous_tool_count = len(state.tool_calls)

            trace = state.trace[-1] if state.trace else {}
            yield {"type": "node_complete", "node": node_name,
                   "duration_ms": trace.get("duration_ms", 0),
                   "next_node": payload.get("next_node"), "timestamp": datetime.now().isoformat()}

        finally:
            _stream_event_queue.reset(queue_token)
            # 客户端断开（async generator 被 aclose）或异常退出时，
            # 取消 producer 任务，终止仍在进行的 LLM 生成，避免空烧调用。
            producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer

        state.execution_time_ms = int((time.time() - start_time) * 1000)
        state.response_ready = True
        state.current_node = AgentNode.END
        yield {"type": "done", "reply": state.reply, "intent": state.detected_intent,
               "execution_time_ms": state.execution_time_ms,
               "tool_calls": [{"tool": tc.tool_name, "success": tc.success} for tc in state.tool_calls],
               "need_human": state.need_human, "timestamp": datetime.now().isoformat()}

    async def run(self, state: AgentState) -> AgentState:
        from app.services.model_gateway import inherited_model_context

        async with inherited_model_context(state.user_id, "graph", route_version=state.strategy_version):
            return await self._run(state)

    async def _run(self, state: AgentState) -> AgentState:
        """Run the compiled LangGraph while preserving the public API."""
        start_time = time.time()
        result = await self.langgraph.ainvoke(
            {"state": state, "next_node": state.current_node},
            config=self._with_langfuse(
                {"recursion_limit": 50, "configurable": {"thread_id": state.session_id}}
            ),
        )
        state = result.get("state", state)
        if result.get("__interrupt__"):
            # The pending preview is enough to continue from a clean turn. Raw
            # Interrupt objects must not leak into the JSON session snapshot.
            state.execution_time_ms = int((time.time() - start_time) * 1000)
            state.response_ready = True
            return state
        state.execution_time_ms = int((time.time() - start_time) * 1000)
        state.response_ready = True
        state.current_node = AgentNode.END
        return state

    async def resume(self, session_id: str, state: AgentState, decision: Any) -> AgentState:
        """Compatibility entry: apply a user decision through the clean turn boundary."""
        if session_id != state.session_id:
            raise ValueError("Session does not match pending action")
        message = "确认" if decision is True else "取消" if decision is False else str(decision)
        return await self.run(state.for_next_turn(state.user_id, message))
