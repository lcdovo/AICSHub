"""
状态机 Agent 编排 - Phase 3 增强版
实现类似 LangGraph 的节点式 Agent 编排
支持条件分支、工具调用、状态追踪、混合检索、结果校验
"""
import time
import json
import asyncio
import contextvars
import logging
import random
from typing import AsyncGenerator, Dict, List, Optional, Any, Callable, TypedDict
from datetime import datetime

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END as LANGGRAPH_END
from langgraph.graph import StateGraph
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

from app.agent.state import AgentState, AgentNode, IntentType
from app.agent.intent import EnhancedIntentRecognizer
from app.agent.tools import ToolExecutionResult
from app.agent.langchain_tools import get_langchain_tool
from app.services.knowledge_base import get_knowledge_base
from app.agent.validation import create_validator
from app.agent.query_rewrite import ConservativeQueryRewriter
from app.agent.query_rewrite_llm import LLMQueryRewriter
from app.agent.validation_llm import LLMRAGJudge
from app.services.user_memory import get_user_memory_service
from app.services.llm_service import LLMService
from app.config.config import settings
from app.utils.langfuse_tracing import get_langfuse_handler

logger = logging.getLogger(__name__)
_stream_event_queue: contextvars.ContextVar[Optional[asyncio.Queue]] = contextvars.ContextVar(
    "agent_stream_event_queue", default=None
)


class AgentGraph:
    """Agent 状态机 - 核心编排器 (Phase 3)"""

    RAG_MIN_SCORE = 0.35

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
        self.checkpointer = InMemorySaver()
        self.langgraph = self._build_langgraph()

    class _GraphState(TypedDict):
        state: AgentState
        next_node: str
        regeneration_count: int

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
        """按请求粒度注入 Langfuse 观测 handler（可配置开关 + 采样率）。

        关闭（默认）、未命中采样或 handler 初始化失败时返回原 config，
        保证外部观测平台不可用绝不影响主链路。
        """
        if not settings.LANGFUSE_ENABLED or random.random() >= settings.LANGFUSE_SAMPLING_RATE:
            return config
        handler = get_langfuse_handler()
        if handler is None:
            return config
        return {**config, "callbacks": [handler]}

    def _make_langgraph_node(self, node_name: str):
        async def invoke(payload: "AgentGraph._GraphState") -> Dict[str, Any]:
            state = payload["state"]
            state.current_node = node_name
            node_start = time.time()
            next_node = await self.nodes[node_name](state)
            regeneration_count = payload.get("regeneration_count", 0)
            state.add_trace(
                node_name=node_name,
                input_data={"message": state.user_message, "intent": state.detected_intent},
                output_data={"next_node": next_node, "reply": state.reply[:100] if state.reply else ""},
                duration_ms=int((time.time() - node_start) * 1000),
            )
            if next_node == AgentNode.END:
                state.current_node = AgentNode.END
            return {"state": state, "next_node": next_node, "regeneration_count": regeneration_count}
        return invoke

    def _register_nodes(self):
        self.nodes[AgentNode.START] = self._node_start
        self.nodes[AgentNode.INTENT_RECOGNITION] = self._node_intent_recognition
        self.nodes[AgentNode.ROUTE_DECISION] = self._node_route_decision
        self.nodes[AgentNode.SLOT_FILLING] = self._node_slot_filling
        self.nodes[AgentNode.QUERY_REWRITE] = self._node_query_rewrite
        self.nodes[AgentNode.CLARIFICATION] = self._node_clarification
        self.nodes[AgentNode.CONFIRMATION] = self._node_confirmation
        self.nodes[AgentNode.RAG_RETRIEVAL] = self._node_rag_retrieval
        self.nodes[AgentNode.TOOL_EXECUTION] = self._node_tool_execution
        self.nodes[AgentNode.RESULT_VERIFICATION] = self._node_result_verification
        self.nodes[AgentNode.RESPONSE_GENERATION] = self._node_response_generation
        self.nodes[AgentNode.HUMAN_HANDOFF] = self._node_human_handoff
        self.nodes[AgentNode.END] = self._node_end

    async def run_stream(self, state: AgentState) -> AsyncGenerator[Dict[str, Any], None]:
        """以同一张 LangGraph 编排图执行流式请求。

        流式接口只负责把图的 updates 转换为事件，不再复制节点、路由或校验逻辑，
        从而保证 ``run`` 与 WebSocket 的副作用和终止条件一致。
        """
        start_time = time.time()
        previous_tool_count = len(state.tool_calls)
        initial_node = state.current_node
        # 保持既有 WebSocket 协议：首事件仍为入口节点，同时附带流式能力标记。
        yield {"type": "node_start", "node": initial_node, "timestamp": datetime.now().isoformat()}
        yield {"type": "stream_mode", "mode": "graph_updates", "token_stream": "langchain", "timestamp": datetime.now().isoformat()}
        first_update = True
        token_index = 0
        event_queue: asyncio.Queue = asyncio.Queue()

        async def produce_updates():
            try:
                async for update in self.langgraph.astream(
                    {"state": state, "next_node": state.current_node, "regeneration_count": 0},
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
                await event_queue.put(("graph_done", None))

        queue_token = _stream_event_queue.set(event_queue)
        producer = asyncio.create_task(produce_updates())
        try:
          graph_done = False
          while not graph_done:
            kind, payload = await event_queue.get()
            if kind == "token":
                content = str(payload or "")
                if content:
                    yield {"type": "token", "content": content, "index": token_index,
                           "streaming": True, "provider": "langchain",
                           "timestamp": datetime.now().isoformat()}
                    token_index += len(content)
                continue
            if kind == "error":
                raise payload
            if kind == "graph_done":
                graph_done = True
                continue
            update = payload
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
            if node_name == AgentNode.RESULT_VERIFICATION and state.collected_info.get("validation"):
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

            if False and node_name == AgentNode.RESPONSE_GENERATION and state.reply:
                # Mock/非 streaming 供应商只能提供可观测的模拟切片，明确标记模式。
                chunk_size = max(1, len(state.reply) // 8)
                for index in range(0, len(state.reply), chunk_size):
                    yield {"type": "token", "content": state.reply[index:index + chunk_size],
                           "index": index, "simulated": True, "timestamp": datetime.now().isoformat()}

        finally:
            _stream_event_queue.reset(queue_token)
            await producer

        state.execution_time_ms = int((time.time() - start_time) * 1000)
        state.response_ready = True
        state.current_node = AgentNode.END
        yield {"type": "done", "reply": state.reply, "intent": state.detected_intent,
               "execution_time_ms": state.execution_time_ms,
               "tool_calls": [{"tool": tc.tool_name, "success": tc.success} for tc in state.tool_calls],
               "need_human": state.need_human, "timestamp": datetime.now().isoformat()}

    async def run(self, state: AgentState) -> AgentState:
        """Run the compiled LangGraph while preserving the public API."""
        start_time = time.time()
        result = await self.langgraph.ainvoke(
            {"state": state, "next_node": state.current_node, "regeneration_count": 0},
            config=self._with_langfuse(
                {"recursion_limit": 50, "configurable": {"thread_id": state.session_id}}
            ),
        )
        state = result.get("state", state)
        if result.get("__interrupt__"):
            state.collected_info["graph_interrupt"] = result["__interrupt__"]
            state.response_ready = True
            return state
        state.execution_time_ms = int((time.time() - start_time) * 1000)
        state.response_ready = True
        state.current_node = AgentNode.END
        return state

    async def resume(self, session_id: str, state: AgentState, decision: Any) -> AgentState:
        """Resume a checkpointed graph after an interrupt decision."""
        try:
            result = await self.langgraph.ainvoke(
                Command(resume=decision),
                config=self._with_langfuse(
                    {"recursion_limit": 50, "configurable": {"thread_id": session_id}}
                ),
            )
        except Exception:
            # A process restart may lose the in-memory checkpoint.  Redis-backed
            # AgentState can still safely resume because the confirmation flag is
            # explicit and the refund tool remains idempotent.
            state.user_message = str(decision)
            state.collected_info["confirm_refund"] = decision is True or str(decision).strip() in {"确认", "同意", "确定", "提交", "是的", "可以", "true", "1"}
            state.collected_info.pop("graph_interrupt", None)
            return await self.run(state)
        resumed = result.get("state", state)
        resumed.collected_info.pop("graph_interrupt", None)
        if result.get("__interrupt__"):
            resumed.collected_info["graph_interrupt"] = result["__interrupt__"]
        resumed.response_ready = True
        resumed.current_node = AgentNode.END
        return resumed

    async def _node_start(self, state: AgentState) -> str:
        return AgentNode.INTENT_RECOGNITION

    async def _node_intent_recognition(self, state: AgentState) -> str:
        if state.collected_info.get("refund_preview") and self._refund_cancelled(state):
            state.detected_intent = IntentType.REFUND
            state.intent_confidence = 1.0
            state.needs_clarification = False
            state.pending_confirmations = []
            state.collected_info.pop("refund_preview", None)
            state.collected_info.pop("confirm_refund", None)
            state.reply = "已取消本次退款申请，未执行任何退款操作。"
            return AgentNode.RESPONSE_GENERATION

        # 确认回复通常只有两个字，优先沿用待确认动作的意图，避免被识别为 general。
        if state.collected_info.get("refund_preview") and self._refund_confirmed(state):
            state.detected_intent = IntentType.REFUND
            state.intent_confidence = 1.0
            state.needs_clarification = False
            return AgentNode.ROUTE_DECISION

        intent_result = self.intent_recognizer.recognize(
            message=state.user_message,
            context=state.messages,
            history_intent=state.detected_intent if state.detected_intent != IntentType.GENERAL else None,
        )

        # 规则优先、LLM 兜底：仅对低置信度输入（<0.4 或需澄清）调用模型二次确认，
        # 关键意图仍由确定性规则主导，避免模型误判导致越权或误操作。
        state.collected_info["intent_source"] = "rules"
        if intent_result.confidence < 0.4 or intent_result.needs_clarification:
            refined = await self.llm_service.classify_intent(
                state.user_message, context=state.messages[-6:]
            )
            if refined and refined.get("confidence", 0) > intent_result.confidence:
                intent_result.intent = refined.get("intent", intent_result.intent)
                intent_result.confidence = float(refined.get("confidence", intent_result.confidence))
                intent_result.needs_clarification = bool(refined.get("need_clarification", False))
                state.collected_info["intent_source"] = "llm_fallback"

        state.detected_intent = intent_result.intent
        state.intent_confidence = intent_result.confidence
        state.needs_clarification = intent_result.needs_clarification

        if intent_result.intent == IntentType.HUMAN:
            state.need_human = True
            if not state.human_reason:
                state.human_reason = "用户请求转接人工客服"

        # 意图序列分析：检测升级模式（如 query_order → refund → complaint）
        intent_history = state.collected_info.get("intent_history", [])
        if intent_history:
            escalation = self._detect_intent_escalation(intent_history, intent_result.intent)
            if escalation and not state.need_human:
                state.need_human = True
                state.human_reason = f"意图升级模式检测: {' -> '.join(escalation)}"

        # 更新意图序列
        updated_history = intent_history + [intent_result.intent]
        state.collected_info["intent_history"] = updated_history[-10:]  # 保留最近10轮

        if intent_result.needs_clarification:
            return AgentNode.CLARIFICATION

        return AgentNode.ROUTE_DECISION

    def _detect_intent_escalation(self, intent_history: List[str], current_intent: str) -> Optional[List[str]]:
        """检测意图升级模式，返回完整升级路径（含当前意图），无升级返回 None。
        升级路径定义：
        - query_order → refund → complaint：查询订单后申请退款再投诉
        - technical → refund → complaint：技术问题转退款再投诉
        - query_order → human：查单后直接转人工
        - refund → complaint：退款后投诉
        - complaint → human：投诉后转人工
        """
        escalation_chains = [
            (["query_order", "refund", "complaint"], "订单→退款→投诉"),
            (["technical", "refund", "complaint"], "技术→退款→投诉"),
            (["query_order", "human"], "订单→人工"),
            (["refund", "complaint"], "退款→投诉"),
            (["complaint", "human"], "投诉→人工"),
        ]

        recent = intent_history[-2:]
        full_sequence = recent + [current_intent]

        for chain, label in escalation_chains:
            if len(full_sequence) >= len(chain):
                tail = full_sequence[-len(chain):]
                if tail == chain:
                    return tail

        return None

    def _route_by_intent(self, intent: str) -> str:
        intent_routes = {
            IntentType.QUERY_ORDER: AgentNode.TOOL_EXECUTION,
            IntentType.REFUND: AgentNode.TOOL_EXECUTION,
            IntentType.COMPLAINT: AgentNode.TOOL_EXECUTION,
            IntentType.TECHNICAL: AgentNode.QUERY_REWRITE,
            IntentType.PROMOTION: AgentNode.QUERY_REWRITE,
            IntentType.HUMAN: AgentNode.HUMAN_HANDOFF,
            IntentType.GENERAL: AgentNode.RESPONSE_GENERATION,
        }
        return intent_routes.get(intent, AgentNode.RESPONSE_GENERATION)

    async def _node_route_decision(self, state: AgentState) -> str:
        """根据意图和已收集槽位选择业务分支。"""
        state.collected_info["user_id"] = state.user_id
        state.collected_info["session_id"] = state.session_id
        extracted = self._extract_info_from_message(state.user_message)
        for key, value in extracted.items():
            if key == "order_id" or key not in state.collected_info or not state.collected_info[key]:
                state.collected_info[key] = value

        # 退款意图分流：政策咨询类（无订单号且询问流程/时效/规则）走 RAG，
        # 实际申请退款（有订单号+动作词）才走工具执行。
        if state.detected_intent == IntentType.REFUND and self._is_refund_policy_inquiry(state.user_message, state.collected_info):
            state.route_reason = "refund:policy_inquiry->rag"
            return AgentNode.QUERY_REWRITE

        missing = self._missing_slots_for_intent(state)
        state.missing_slots = missing
        if missing:
            state.route_reason = f"missing_slots:{','.join(missing)}"
            return AgentNode.SLOT_FILLING

        next_node = self._route_by_intent(state.detected_intent)
        state.route_reason = f"intent:{state.detected_intent}->{next_node}"
        return next_node

    def _is_refund_policy_inquiry(self, message: str, collected: Dict[str, Any]) -> bool:
        """判断退款意图是否为政策咨询（应走 RAG）而非实际退款申请（应走工具）。"""
        policy_keywords = ["流程", "几天", "多久", "怎么退", "如何退", "政策", "规则", "到账", "无理由", "质保期", "保修期", "售后", "换货流程", "退货流程", "运费谁出", "能退吗"]
        action_keywords = ["申请", "退钱", "帮我退", "给我退", "我要退", "直接退", "马上退", "退款成功"]
        has_policy_kw = any(kw in message for kw in policy_keywords)
        has_action_kw = any(kw in message for kw in action_keywords)
        has_order_id = bool(collected.get("order_id"))
        # 有政策词且没有明确动作词、且没有订单号 → 政策咨询
        if has_policy_kw and not has_action_kw and not has_order_id:
            return True
        # "能退吗/可以退吗" 这类询问是否可退，无订单号 → 政策咨询
        if any(kw in message for kw in ["能退吗", "可以退", "好退吗", "给退吗"]) and not has_order_id:
            return True
        return False

    def _missing_slots_for_intent(self, state: AgentState) -> List[str]:
        collected = state.collected_info
        if state.detected_intent == IntentType.QUERY_ORDER:
            return ["order_id"] if not collected.get("order_id") else []
        if state.detected_intent == IntentType.REFUND:
            missing = []
            if not collected.get("order_id"):
                missing.append("order_id")
            reason = str(collected.get("reason") or "").strip()
            if not reason or len(reason) < 6:
                missing.append("reason")
            return missing
        return []

    async def _node_slot_filling(self, state: AgentState) -> str:
        """提示用户补齐业务工具所需的最小参数。"""
        prompts = {"order_id": "订单号", "reason": "退款原因"}
        labels = [prompts.get(slot, slot) for slot in state.missing_slots]
        if state.detected_intent == IntentType.REFUND:
            state.reply = f"为了处理退款申请，请补充：{'、'.join(labels)}。"
        else:
            state.reply = f"为了查询订单，请提供：{'、'.join(labels)}。"
        return AgentNode.RESPONSE_GENERATION

    async def _node_query_rewrite(self, state: AgentState) -> str:
        """为 RAG 补全多轮上下文，不修改用户原文或工具参数。

        组合改写：保守规则优先，规则未命中且 LLM 可用时由模型做语义改写。
        """
        result = await self.llm_rewriter.rewrite(
            query=state.user_message,
            messages=state.messages,
            collected_info=state.collected_info,
        )

        state.rewritten_query = result.rewritten_query
        state.query_rewrite_applied = result.applied
        state.query_rewrite_reason = result.reason
        state.collected_info["query_rewrite"] = {
            "original_query": result.original_query,
            "rewritten_query": result.rewritten_query,
            "applied": result.applied,
            "reason": result.reason,
            "confidence": result.confidence,
        }
        return AgentNode.RAG_RETRIEVAL

    async def _node_clarification(self, state: AgentState) -> str:
        question = self.intent_recognizer.get_clarification_question(state.detected_intent)
        state.reply = question
        state.needs_clarification = True

        state.messages.append({
            "role": "assistant",
            "content": question,
            "type": "clarification",
        })

        return AgentNode.RESPONSE_GENERATION

    async def _node_rag_retrieval(self, state: AgentState) -> str:
        query = state.rewritten_query or state.user_message
        top_k = 3

        # The chat path and the admin knowledge APIs must share one source of truth.
        # KnowledgeBaseService loads published documents from SQLite/Milvus and keeps
        # the in-memory fallback when external services are unavailable.
        knowledge_base = get_knowledge_base()
        if hasattr(knowledge_base, "async_search"):
            search_result = await knowledge_base.async_search(query=query, top_k=top_k)
        else:
            search_result = await asyncio.to_thread(knowledge_base.search, query=query, top_k=top_k)

        state.add_tool_call(
            tool_name="hybrid_search",
            arguments={"query": query, "top_k": top_k},
            result=search_result,
            success=search_result.get("success", False),
        )

        state.collected_info["kb_results"] = search_result.get("results", [])
        state.collected_info["kb_search_meta"] = search_result.get("meta", {})

        if state.collected_info["kb_results"]:
            best_match = state.collected_info["kb_results"][0]
            state.collected_info["best_match_score"] = best_match.get(
                "final_score", best_match.get("hybrid_score", 0)
            )

        score = float(state.collected_info.get("best_match_score") or 0)
        if not state.collected_info["kb_results"] or score < self.RAG_MIN_SCORE:
            state.collected_info["rag_grounded"] = False
            state.collected_info["rag_fallback_reason"] = (
                "no_results" if not state.collected_info["kb_results"] else "low_score"
            )
            state.reply = (
                "我暂时没有在知识库中找到足够可靠的答案。"
                "请补充更多细节，或回复“转人工”获取客服协助。"
            )
        else:
            state.collected_info["rag_grounded"] = True

        logger.info(
            f"混合检索完成: {len(search_result.get('results', []))} 条结果, "
            f"耗时 {search_result.get('execution_time_ms', 0)}ms"
        )

        return AgentNode.RESPONSE_GENERATION

    async def _node_tool_execution(self, state: AgentState) -> str:
        state.collected_info["user_id"] = state.user_id
        state.collected_info["session_id"] = state.session_id
        extracted_info = self._extract_info_from_message(state.user_message)

        for key, value in extracted_info.items():
            if key == "order_id" or key not in state.collected_info or not state.collected_info[key]:
                state.collected_info[key] = value

        tools_to_execute = self._select_tools_for_intent(
            state.detected_intent, state.collected_info
        )

        # 退款是不可逆副作用：第一次请求只生成预览，必须在独立确认节点中得到
        # 明确同意后才能调用工具。确认状态放在 AgentState 中，跨普通/流式入口一致。
        if state.detected_intent == IntentType.REFUND and tools_to_execute:
            if not self._refund_confirmed(state):
                state.pending_confirmations = ["apply_refund"]
                state.collected_info["refund_preview"] = tools_to_execute[0][1]
                return AgentNode.CONFIRMATION
            state.pending_confirmations = []

        for tool_name, tool_args in tools_to_execute:
            tool = get_langchain_tool(tool_name)
            if tool:
                try:
                    raw_result = await tool.ainvoke(tool_args)
                    result = ToolExecutionResult.model_validate(raw_result)

                    state.add_tool_call(
                        tool_name=tool_name,
                        arguments=tool_args,
                        result={
                            "success": result.success,
                            "data": result.data,
                            "error": result.error,
                            "hint": result.hint,
                            "execution_time_ms": result.execution_time_ms,
                            "retry_count": result.retry_count,
                            "error_type": result.error_type,
                        },
                        success=result.success,
                        error_message=result.error,
                        error_type=result.error_type,
                    )

                    if result.success and result.data:
                        if isinstance(result.data, dict):
                            state.collected_info.update(result.data)
                        state.collected_info[f"{tool_name}_result"] = result.data

                    if not result.success:
                        if result.error and "人工" in result.error:
                            state.need_human = True
                            state.human_reason = result.error
                            return AgentNode.HUMAN_HANDOFF

                        if result.retry_count >= 3:
                            state.need_human = True
                            state.human_reason = f"工具 {tool_name} 连续 {result.retry_count} 次失败"
                            logger.warning(f"工具连续失败，转人工: {state.human_reason}")
                            return AgentNode.HUMAN_HANDOFF

                except Exception as e:
                    logger.error(f"工具 {tool_name} 执行异常: {e}")
                    state.add_tool_call(
                        tool_name=tool_name,
                        arguments=tool_args,
                        result=None,
                        success=False,
                        error_message=str(e),
                    )

        return AgentNode.RESULT_VERIFICATION

    def _refund_confirmed(self, state: AgentState) -> bool:
        """识别显式确认，避免将“好的/谢谢”等普通回复当作授权。"""
        if state.collected_info.get("confirm_refund") is True:
            return True
        confirmations = {"确认", "同意", "确定", "提交", "是的", "可以"}
        message = state.user_message.strip()
        return message in confirmations or any(
            msg.get("role") == "user" and msg.get("content", "").strip() in confirmations
            for msg in state.messages[-3:]
        )

    @staticmethod
    def _refund_cancelled(state: AgentState) -> bool:
        return state.user_message.strip() in {"取消", "不同意", "先不退", "不提交", "算了", "false", "0"}

    async def _node_confirmation(self, state: AgentState) -> str:
        preview = state.collected_info.get("refund_preview", {})
        if self._refund_confirmed(state):
            state.collected_info["confirm_refund"] = True
            state.pending_confirmations = []
            return AgentNode.TOOL_EXECUTION

        order_id = preview.get("order_id", "")
        refund_type = preview.get("type", "refund")
        reason = preview.get("reason", "")[:80]
        label = "退货退款" if refund_type in {"return", "exchange"} else "仅退款"
        state.reply = (
            f"即将为订单 {order_id} 提交{label}申请，原因：{reason or '未填写'}。"
            "该操作提交后将进入审核，是否确认提交？（请回复“确认”）"
        )
        decision = interrupt({
            "type": "refund_confirmation",
            "order_id": order_id,
            "refund_type": refund_type,
            "reason": reason,
        })
        if decision is True or str(decision).strip() in {"确认", "同意", "确定", "提交", "是的", "可以", "true", "1"}:
            state.collected_info["confirm_refund"] = True
            state.pending_confirmations = []
            return AgentNode.TOOL_EXECUTION
        return AgentNode.RESPONSE_GENERATION

    def _extract_info_from_message(self, message: str) -> Dict[str, Any]:
        import re

        info = {}

        order_patterns = [
            r"ORD\d{6,}",
            r"订单[：:]\s*(\w+)",
            r"订单号[：:]\s*(\w+)",
        ]
        for pattern in order_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                order_id = match.group(0) if len(match.groups()) == 0 else match.group(1)
                info["order_id"] = order_id
                break

        if any(word in message for word in ["投诉", "垃圾", "骗子", "气愤"]):
            info["complaint_content"] = message

        if any(word in message for word in ["退款", "退货", "退换"]):
            info["reason"] = message
            if "换" in message:
                info["refund_type"] = "exchange"
            else:
                info["refund_type"] = "refund"

        return info

    def _select_tools_for_intent(
        self, intent: str, collected_info: Dict[str, Any]
    ) -> List[tuple]:
        tool_plan = []

        if intent == IntentType.QUERY_ORDER:
            order_id = collected_info.get("order_id")
            if order_id:
                tool_plan.append(("query_order", {"order_id": order_id, "user_id": collected_info.get("user_id", 0)}))

        elif intent == IntentType.REFUND:
            order_id = collected_info.get("order_id", "")
            reason = collected_info.get("reason", "")
            refund_type = collected_info.get("refund_type", "refund")

            if order_id:
                tool_plan.append((
                    "apply_refund",
                    {
                        "order_id": order_id,
                        "reason": reason,
                        "type": refund_type,
                        "user_id": collected_info.get("user_id", 0),
                        "idempotency_key": f"refund:{collected_info.get('session_id', '')}:{order_id}:{refund_type}",
                    },
                ))

        elif intent == IntentType.COMPLAINT:
            complaint_content = collected_info.get("complaint_content", "")
            tool_plan.append((
                "create_ticket",
                {
                    "user_id": collected_info.get("user_id", 1),
                    "category": "投诉建议",
                    "content": complaint_content or "用户投诉",
                    "priority": "high",
                },
            ))

        return tool_plan

    async def _node_result_verification(self, state: AgentState) -> str:
        if state.tool_calls:
            last_call = state.tool_calls[-1]

            if not last_call.success:
                tool_count = len(state.tool_calls)
                if tool_count < 3:
                    state.reply = f"抱歉，操作遇到问题：{last_call.error_message or '未知错误'}"
                else:
                    state.need_human = True
                    state.human_reason = "工具调用连续失败"
                    logger.warning(f"工具连续失败 ({tool_count}次)，转人工")
                    return AgentNode.HUMAN_HANDOFF

            if last_call.success and isinstance(last_call.result, dict):
                execution_time = last_call.result.get("execution_time_ms", 0)
                retry_count = last_call.result.get("retry_count", 0)
                if execution_time > 3000:
                    logger.warning(
                        f"工具执行耗时过长: {execution_time}ms"
                    )

                if retry_count > 0:
                    logger.info(
                        f"工具执行重试 {retry_count} 次后成功"
                    )

        return AgentNode.RESPONSE_GENERATION

    async def _node_response_generation(self, state: AgentState) -> str:
        if state.need_human and not state.reply:
            state.reply = f"非常抱歉，正在为您转接人工客服。{state.human_reason or ''}"

        # 注入跨会话用户画像，使 LLM 能结合用户历史作答（本文改进第 5 项）。
        if settings.USER_MEMORY_ENABLED and not state.collected_info.get("user_memory"):
            try:
                state.collected_info["user_memory"] = await get_user_memory_service().get_user_profile(
                    user_id=state.user_id,
                    query=state.user_message,
                    session_id=state.session_id,
                )
            except Exception:
                state.collected_info["user_memory"] = {"memory_source": "degraded"}

        generated_by_llm = False
        if not state.reply:
            llm_reply = await self._generate_response_with_llm(state)
            if llm_reply:
                state.reply = llm_reply
                generated_by_llm = True
            else:
                state.reply = self._get_default_reply(state.detected_intent, state.user_message)

        # Validate the actual final text.  The old flow validated before this node,
        # so normal LLM output never went through fact/safety/completeness checks.
        if state.collected_info.get("kb_results") and state.collected_info.get("rag_grounded", True):
            state.reply = self._add_rag_citations(state.reply, state.collected_info["kb_results"])

        # LLM-as-judge 防幻觉：对 RAG 生成回答做证据一致性核查，
        # 无法锚定证据时选择"无据不答"，而非让模型为生成而编造（本文改进第 2 项）。
        if generated_by_llm and self._rag_judge_applicable(state):
            judgement = await self.llm_judge.judge(
                response=state.reply,
                user_query=state.user_message,
                kb_context=state.collected_info.get("kb_results", []),
                tool_evidence=self._tool_evidence_text(state),
            )
            if judgement:
                state.collected_info["llm_judge"] = judgement
                if judgement.get("refuse_recommended"):
                    state.collected_info["rag_refused"] = True
                    state.reply = (
                        "抱歉，我暂时没有足够可靠的依据回答这个问题。"
                        "为避免误导，建议您补充更具体的信息，"
                        "或回复“转人工”由客服协助确认。"
                    )

        # 槽位追问是流程提示，不是业务答案，不应因"信息尚未齐全"被判定为低质量并转人工。
        if (generated_by_llm or not state.collected_info.get("validation")) and not state.missing_slots and not state.collected_info.get("rag_refused"):
            validation = self.validator.validate(
                response=state.reply,
                user_query=state.user_message,
                intent=state.detected_intent,
                tool_results=[tc.result for tc in state.tool_calls],
            )
            state.collected_info["validation"] = validation.to_dict()
            if validation.needs_regeneration and generated_by_llm:
                retry_reply = await self._generate_response_with_llm(state)
                if retry_reply:
                    state.reply = retry_reply
                    if state.collected_info.get("kb_results"):
                        state.reply = self._add_rag_citations(state.reply, state.collected_info["kb_results"])
                    validation = self.validator.validate(
                        response=state.reply,
                        user_query=state.user_message,
                        intent=state.detected_intent,
                        tool_results=[tc.result for tc in state.tool_calls],
                    )
                    state.collected_info["validation"] = validation.to_dict()
            if not validation.passed:
                state.need_human = True
                state.human_reason = "生成后回答校验未通过"
                return AgentNode.HUMAN_HANDOFF

        state.messages.append({
            "role": "assistant",
            "content": state.reply,
            "intent": state.detected_intent,
            "tool_calls": [tc.tool_name for tc in state.tool_calls],
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
        })

        return AgentNode.END

    def _add_rag_citations(self, reply: str, results: list[dict[str, Any]]) -> str:
        """Expose the exact published chunks used by the answer."""
        if "参考来源" in reply:
            return reply
        citations = []
        for item in results[:3]:
            title = item.get("title", "未命名文档")
            snippet = " ".join(str(item.get("content", "")).split())[:120]
            citations.append(f"- {title}: {snippet}")
        return reply + ("\n\n参考来源：\n" + "\n".join(citations) if citations else "")

    _TOOL_ANSWER_INTENTS = {
        IntentType.QUERY_ORDER,
        IntentType.COMPLAINT,
        IntentType.REFUND,
    }

    def _rag_judge_applicable(self, state: AgentState) -> bool:
        """仅对证据型（RAG）回答启用 LLM judge；工具型回答已有启发式事实校验。"""
        return (
            bool(state.collected_info.get("rag_grounded"))
            and not state.missing_slots
            and state.detected_intent not in self._TOOL_ANSWER_INTENTS
        )

    @staticmethod
    def _tool_evidence_text(state: AgentState) -> str:
        """把成功工具返回的数据压缩成给 judge 的证据文本。"""
        parts = []
        for tc in state.tool_calls:
            if tc.success and isinstance(tc.result, dict) and tc.result.get("data"):
                parts.append(json.dumps(tc.result["data"], ensure_ascii=False)[:300])
        return "\n".join(parts)

    async def _generate_response_with_llm(self, state: AgentState) -> str:
        """使用 LLM 生成响应，失败时返回 None 由调用方降级"""
        try:
            prompt = self._build_llm_prompt(state)
            context_messages = self._build_context_messages(state)

            payload = {
                "user_id": state.user_id,
                "session_id": state.session_id,
                "message": prompt,
                "context": context_messages,
            }
            stream_queue = _stream_event_queue.get()
            if stream_queue is not None:
                chunks = []
                async for chunk in self.llm_service.stream_chat(**payload):
                    chunks.append(chunk)
                    await stream_queue.put(("token", chunk))
                reply = "".join(chunks)
                metadata = self.llm_service.get_last_call_metadata()
                structured = metadata.get("structured_output", {})
                intent = structured.get("intent") or self.llm_service.detect_intent(prompt)
                token_count = int(metadata.get("tokens", max(1, len(reply) // 4)))
            else:
                reply, intent, token_count = await self.llm_runnable.ainvoke(payload)
            state.total_tokens = token_count
            # 治理元数据只包含摘要、版本和预算结果，不保存发送给模型的敏感原文。
            state.collected_info["llm_governance"] = self.llm_service.get_last_call_metadata()

            if state.detected_intent not in (IntentType.HUMAN, IntentType.COMPLAINT, IntentType.QUERY_ORDER, IntentType.REFUND):
                state.detected_intent = intent

            logger.info(
                f"LLM 响应生成成功: intent={state.detected_intent}, tokens={token_count}, "
                f"session={state.session_id}"
            )
            return reply
        except Exception as e:
            logger.warning(f"LLM 调用失败: {e}")
            return None

    async def _invoke_llm_service(self, payload: Dict[str, Any]):
        return await self.llm_service.chat(**payload)

    def _build_llm_prompt(self, state: AgentState) -> str:
        """构建发送给 LLM 的富上下文 prompt"""
        parts = []
        intent_labels = {
            "query_order": "订单查询",
            "refund": "退换货",
            "complaint": "投诉",
            "technical": "技术咨询",
            "promotion": "活动咨询",
            "human": "转人工",
            "general": "通用咨询",
        }
        intent_label = intent_labels.get(state.detected_intent, state.detected_intent)
        parts.append(f"【用户意图】{intent_label}")
        parts.append(f"【用户消息】{state.user_message}")

        # 注入跨会话用户画像，帮助模型结合用户历史与偏好作答（改进第 5 项）。
        user_memory = state.collected_info.get("user_memory")
        if isinstance(user_memory, dict) and user_memory.get("memory_source") not in (None, "none"):
            mem_parts = []
            mem_parts.append(f"用户等级：{user_memory.get('user_level', '普通')}")
            tags = user_memory.get("tags") or []
            if tags:
                mem_parts.append(f"用户标签：{'、'.join(str(t) for t in tags[:5])}")
            recalled = user_memory.get("recalled") or []
            if recalled:
                for item in recalled[:3]:
                    mem_parts.append(f"近期相关记录：{str(item.get('content', ''))[:120]}")
            if mem_parts:
                parts.append("【用户画像】" + "；".join(mem_parts))

        successful_tools = [tc for tc in state.tool_calls if tc.success]
        if successful_tools:
            parts.append("【工具执行结果】")
            for tc in successful_tools:
                result = tc.result
                if isinstance(result, dict) and result.get("data"):
                    data = result["data"]
                    if isinstance(data, dict):
                        parts.append(f"  - {tc.tool_name}: {json.dumps(data, ensure_ascii=False, indent=2)}")
                    elif isinstance(data, list) and data:
                        if isinstance(data[0], dict):
                            parts.append(f"  - {tc.tool_name}: 找到 {len(data)} 条结果")
                            for item in data[:3]:
                                title = item.get("title", "")
                                content = item.get("content", "")[:200]
                                if title:
                                    parts.append(f"    【{title}】{content}")
                        else:
                            parts.append(f"  - {tc.tool_name}: {json.dumps(data, ensure_ascii=False)[:500]}")
                    else:
                        parts.append(f"  - {tc.tool_name}: {str(data)[:500]}")
                elif isinstance(result, dict) and result.get("success"):
                    parts.append(f"  - {tc.tool_name}: 操作成功")
                else:
                    parts.append(f"  - {tc.tool_name}: 执行成功")

        failed_tools = [tc for tc in state.tool_calls if not tc.success]
        if failed_tools:
            parts.append("【工具执行失败】")
            for tc in failed_tools:
                parts.append(f"  - {tc.tool_name}: {tc.error_message or '未知错误'}")

        kb_results = state.collected_info.get("kb_results", [])
        if kb_results:
            parts.append("【知识库检索结果】")
            for i, result in enumerate(kb_results[:3], 1):
                title = result.get("title", "")
                content = result.get("content", "")
                category = result.get("category", "")
                score = result.get("final_score", result.get("hybrid_score", 0))
                parts.append(f"  {i}. 【{title}】({category}, 相关度:{score:.0%})")
                if content:
                    parts.append(f"     {content[:300]}")

        parts.append("")
        parts.append("请根据以上信息，用自然、专业、简洁的中文回复用户。回复要点：")
        parts.append("1. 直接回应用户的问题或需求")
        parts.append("2. 如果有工具执行结果，清晰告知用户结果")
        parts.append("3. 如果有知识库内容，基于检索内容给出准确回答")
        parts.append("4. 保持友好专业的语气，适当引导下一步操作")
        parts.append("5. 回复控制在 200 字以内")

        return "\n".join(parts)

    def _build_context_messages(self, state: AgentState) -> list:
        """构建对话历史上下文"""
        context = []
        for msg in state.messages[-6:]:
            if msg.get("role") in ("user", "assistant"):
                context.append({
                    "role": msg["role"],
                    "content": msg.get("content", ""),
                })
        return context

    def _get_default_reply(self, intent: str, message: str) -> str:
        if intent == IntentType.GENERAL:
            msg = (message or "").strip().lower()
            if any(g in msg for g in ["你好", "您好", "hi", "hello", "在吗", "在不在", "哈喽", "嗨"]):
                return "您好，请问有什么可以帮您？"
            if any(t in msg for t in ["谢谢", "感谢", "多谢", "thanks", "thank"]):
                return "不客气，还有其他可以帮您的吗？"
            return "我可以帮助您查询订单、处理退换货、解答产品问题等。请问有什么可以帮您的？"
        default_replies = {
            IntentType.QUERY_ORDER: "请提供您的订单号，我可以帮您查询最新的订单状态和物流信息。",
            IntentType.REFUND: "关于退换货问题，请提供订单号和退换货原因，我将为您处理。",
            IntentType.COMPLAINT: "非常抱歉给您带来不好的体验。请详细描述您遇到的问题，我们会尽快为您解决。",
            IntentType.TECHNICAL: "我来帮您解决这个问题。请详细描述遇到的情况，包括操作步骤和报错信息。",
            IntentType.PROMOTION: "我们目前有多种优惠活动。您可以在官网或 APP 首页查看最新的促销信息。",
        }
        return default_replies.get(intent, "我可以帮助您查询订单、处理退换货、解答产品问题等。请问有什么可以帮您的？")

    async def _node_human_handoff(self, state: AgentState) -> str:
        tool = get_langchain_tool("escalate_to_human")
        if tool:
            result = await tool.ainvoke({
                "reason": state.human_reason or "用户要求转人工",
                "priority": "urgent" if state.need_human else "normal",
                "user_id": state.user_id,
                "session_id": state.session_id,
            })
            result = ToolExecutionResult.model_validate(result).model_dump()
            state.add_tool_call(
                tool_name="escalate_to_human",
                arguments={"reason": state.human_reason},
                result=result,
                success=result.get("success", False),
            )
            state.reply = result.get("data", {}).get("message", "正在为您转接人工客服...")
        else:
            state.reply = "正在为您转接人工客服，请稍候..."

        return AgentNode.RESPONSE_GENERATION

    async def _node_end(self, state: AgentState) -> str:
        return AgentNode.END
