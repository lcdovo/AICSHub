"""RAG 链路节点：查询改写与混合检索。"""
import asyncio
import logging

from app.agent.state import AgentState, AgentNode
from app.config.config import settings

# 经 graph 模块转发取知识库工厂：graph.py 保持模块级 get_knowledge_base
# 再导出（兼容既有测试 monkeypatch 路径），本节点在调用时动态解析该名字，
# 使测试/调用方能替换工厂实现。
import app.agent.graph as _graph_module

logger = logging.getLogger(__name__)


async def query_rewrite_node(self: "AgentGraph", state: AgentState) -> str:
    """为 RAG 补全多轮上下文，不修改用户原文或工具参数。

    组合改写：保守规则优先，规则未命中且 LLM 可用时由模型做语义改写。
    """
    if state.understanding is not None:
        state.rewritten_query=state.understanding.retrieval_query
        state.query_rewrite_reason="unified_understanding"
        return AgentNode.RAG_RETRIEVAL
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
        "simple": result.simple,
    }
    return AgentNode.RAG_RETRIEVAL


async def rag_retrieval_node(self: "AgentGraph", state: AgentState) -> str:
    query = state.rewritten_query or state.user_message
    top_k = settings.RAG_TOP_K

    # The chat path and the admin knowledge APIs must share one source of truth.
    # KnowledgeBaseService loads published documents from SQLite/Milvus and keeps
    # the in-memory fallback when external services are unavailable.
    knowledge_base = await asyncio.to_thread(_graph_module.get_knowledge_base)
    if hasattr(knowledge_base, "async_search"):
        search_result = await knowledge_base.async_search(query=query, top_k=top_k)
    else:
        search_result = await asyncio.to_thread(knowledge_base.search, query=query, top_k=top_k)

    if not isinstance(search_result, dict):
        search_result = {"success": False, "results": [], "meta": {}}
    contract_ok = state.accept_retrieval(search_result)
    recorded_result = search_result if contract_ok else {
        "success": False, "results": [], "meta": state.retrieval_metadata,
        "error": state.retrieval_error, "evidence_bundle": state.evidence_bundle.model_dump(),
    }
    state.add_tool_call(
        tool_name="hybrid_search",
        arguments={"query": query, "top_k": top_k},
        result=recorded_result,
        success=contract_ok,
    )

    if state.collected_info["kb_results"]:
        best_match = state.collected_info["kb_results"][0]
        state.collected_info["best_match_score"] = best_match.get(
            "final_score", best_match.get("hybrid_score", 0)
        )

    score = float(state.collected_info.get("best_match_score") or 0)
    score_type=state.retrieval_metadata.get("score_type")
    if not state.collected_info["kb_results"] or (score_type != "rrf" and score < self.RAG_MIN_SCORE):
        state.collected_info["rag_grounded"] = False
        state.collected_info["rag_fallback_reason"] = (
            state.retrieval_error or ("no_results" if not state.collected_info["kb_results"] else "low_score")
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
