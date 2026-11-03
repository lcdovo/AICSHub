import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock
from app.agent.graph import AgentGraph
from app.agent.state import AgentState, AgentNode
from app.agent.query_rewrite import QueryRewriteResult
from app.agent.nodes.rag_node import (
    QueryRewriteNode, QueryRewriteDependencies, RetrievalNode, RetrievalDependencies,
)
from tests.test_m4_understanding import result as understanding


async def test_rewriter_and_retrieval_execute_with_only_declared_services():
    state = AgentState(session_id="s", user_id=101, user_message="它的运费呢？")
    rewritten = "耳机退货运费规则"
    rewrite = AsyncMock(return_value=QueryRewriteResult(state.user_message, rewritten, True, "synthetic"))
    rewrite_node = QueryRewriteNode(QueryRewriteDependencies(SimpleNamespace(rewrite=rewrite)))
    assert await rewrite_node(state) == AgentNode.RAG_RETRIEVAL
    assert state.user_message == "它的运费呢？" and state.rewritten_query == rewritten
    search = AsyncMock(return_value={"success": True, "results": [], "meta": {}})
    node = RetrievalNode(RetrievalDependencies(lambda: SimpleNamespace(async_search=search), lambda: 7, .35))
    assert await node(state) == AgentNode.RESPONSE_GENERATION
    search.assert_awaited_once_with(query=rewritten, top_k=7)
    assert state.collected_info["rag_grounded"] is False


async def test_unified_understanding_does_not_repeat_rewrite_model_call():
    rewrite = AsyncMock(side_effect=AssertionError("unified query must not call rewriter again"))
    value = understanding(retrieval_query="统一理解保留耳机退款时效")
    state = AgentState(session_id="s", user_id=101, user_message="多久", understanding=value)
    node = QueryRewriteNode(QueryRewriteDependencies(SimpleNamespace(rewrite=rewrite)))
    assert await node(state) == AgentNode.RAG_RETRIEVAL
    assert state.rewritten_query == value.retrieval_query
    rewrite.assert_not_awaited()


async def test_sync_knowledge_factory_and_search_do_not_block_event_loop_thread():
    loop_thread = threading.get_ident()
    observed = []

    class SyncKB:
        def search(self, **kwargs):
            observed.append(threading.get_ident())
            return {"success": True, "results": [], "meta": {}}

    def factory():
        observed.append(threading.get_ident())
        return SyncKB()

    node = RetrievalNode(RetrievalDependencies(factory, lambda: 3, .35))
    await node(AgentState(session_id="s", user_id=101, user_message="合成查询"))
    assert len(observed) == 2 and all(value != loop_thread for value in observed)


async def test_main_graph_runs_registered_dependencies_without_legacy_private_helpers(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    graph = AgentGraph()
    rewrite = AsyncMock(return_value=QueryRewriteResult("耳机如何保养", "耳机保养规则", True, "synthetic"))
    search = AsyncMock(return_value={"success": True, "results": [], "meta": {}})
    graph.llm_rewriter = SimpleNamespace(rewrite=rewrite)
    monkeypatch.setattr("app.agent.graph.get_knowledge_base", lambda: SimpleNamespace(async_search=search))
    graph._node_query_rewrite = AsyncMock(side_effect=AssertionError("legacy helper used"))
    graph._node_rag_retrieval = AsyncMock(side_effect=AssertionError("legacy helper used"))
    graph._register_nodes()
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    graph.llm_service.classify_intent = AsyncMock(return_value=None)
    monkeypatch.setattr(graph.llm_service, "is_available", lambda: False)
    try:
        state = await graph.run(AgentState(session_id="s", user_id=101, user_message="耳机如何保养"))
        assert state.current_node == AgentNode.END and state.response_ready
        rewrite.assert_awaited_once()
        search.assert_awaited_once()
        graph._node_query_rewrite.assert_not_awaited()
        graph._node_rag_retrieval.assert_not_awaited()
    finally:
        await graph.llm_service.close()
