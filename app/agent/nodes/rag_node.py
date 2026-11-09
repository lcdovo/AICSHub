"""RAG 链路节点：查询改写与混合检索。"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Protocol, Any

from app.agent.state import AgentState, AgentNode
from app.config.config import settings

from app.agent.query_rewrite import QueryRewriteResult

logger = logging.getLogger(__name__)


class Rewriter(Protocol):
    async def rewrite(self, *, query: str, messages: list[dict], collected_info: dict) -> QueryRewriteResult: ...


class AsyncKnowledgeSearch(Protocol):
    async def async_search(self, *, query: str, top_k: int) -> dict[str, Any]: ...


class SyncKnowledgeSearch(Protocol):
    def search(self, *, query: str, top_k: int) -> dict[str, Any]: ...


@dataclass(frozen=True)
class QueryRewriteDependencies:
    rewriter: Rewriter


@dataclass(frozen=True)
class RetrievalDependencies:
    knowledge_factory: Callable[[], AsyncKnowledgeSearch | SyncKnowledgeSearch]
    top_k: Callable[[], int]
    minimum_score: float


@dataclass(frozen=True)
class QueryRewriteNode:
    dependencies: QueryRewriteDependencies

    async def __call__(self, state: AgentState) -> str:
        return await rewrite_query(self.dependencies, state)


@dataclass(frozen=True)
class RetrievalNode:
    dependencies: RetrievalDependencies

    async def __call__(self, state: AgentState) -> str:
        return await retrieve_evidence(self.dependencies, state)


async def rewrite_query(dependencies: QueryRewriteDependencies, state: AgentState) -> str:
    """为 RAG 补全多轮上下文，不修改用户原文或工具参数。

    组合改写：保守规则优先，规则未命中且 LLM 可用时由模型做语义改写。
    """
    if state.understanding is not None:
        query = state.understanding.retrieval_query
        state.record_query_rewrite({
            "original_query": state.user_message, "rewritten_query": query,
            "applied": " ".join(query.split()) != " ".join(state.user_message.split()),
            "reason": "unified_understanding", "confidence": 0., "simple": False,
        }, "unified_understanding")
        return AgentNode.RAG_RETRIEVAL
    result = await dependencies.rewriter.rewrite(
        query=state.user_message,
        messages=state.messages,
        collected_info=state.current_business_parameters().model_dump(exclude_none=True),
    )

    state.record_query_rewrite({key: getattr(result, key, None) for key in (
        "original_query", "rewritten_query", "applied", "reason", "confidence", "simple",
    )}, "rewriter")
    return AgentNode.RAG_RETRIEVAL


async def retrieve_evidence(dependencies: RetrievalDependencies, state: AgentState) -> str:
    query = state.current_retrieval_query()
    top_k = dependencies.top_k()

    # The chat path and the admin knowledge APIs must share one source of truth.
    # KnowledgeBaseService loads published documents from SQLite/Milvus and keeps
    # the in-memory fallback when external services are unavailable.
    knowledge_base = await asyncio.to_thread(dependencies.knowledge_factory)
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
    if not state.collected_info["kb_results"] or (score_type != "rrf" and score < dependencies.minimum_score):
        state.set_flow(rag_grounded=False)
        state.collected_info["rag_fallback_reason"] = (
            state.retrieval_error or ("no_results" if not state.collected_info["kb_results"] else "low_score")
        )
        state.set_workflow_reply("我暂时没有在知识库中找到足够可靠的答案。"
            "请补充更多细节，或回复“转人工”获取客服协助。")
        admission = search_result.get("meta", {}).get("local_model_admission")
        if admission == "busy":
            state.set_workflow_reply("本地检索模型繁忙，本轮未取得足够可靠的依据。请稍后重试；尚未创建人工或其他业务请求。")
        elif admission == "deadline":
            state.set_workflow_reply("本地检索模型等待或推理超时，本轮未核实答案。请稍后重试；尚未创建人工或其他业务请求。")
        elif (search_result.get("meta", {}).get("product_scope") or {}).get("reason") == "device_not_in_retrieved_sources":
            state.set_workflow_reply("本轮检索未取得与该设备匹配的操作依据。请补充设备品牌、型号和具体故障现象；在核实前我不会提供拆机或重置步骤，尚未创建人工请求。")
    else:
        state.set_flow(rag_grounded=True)

    logger.info(
        f"混合检索完成: {len(search_result.get('results', []))} 条结果, "
        f"耗时 {search_result.get('execution_time_ms', 0)}ms"
    )

    return AgentNode.RESPONSE_GENERATION


# 仅供原私有方法/旧内部调用兼容。主图注册的节点直接接收显式依赖，不导入graph。
async def query_rewrite_node(graph, state: AgentState) -> str:
    return await rewrite_query(QueryRewriteDependencies(graph.llm_rewriter), state)


async def rag_retrieval_node(graph, state: AgentState) -> str:
    from app.agent.graph import get_knowledge_base
    return await retrieve_evidence(RetrievalDependencies(
        knowledge_factory=get_knowledge_base, top_k=lambda: settings.RAG_TOP_K,
        minimum_score=graph.RAG_MIN_SCORE,
    ), state)
