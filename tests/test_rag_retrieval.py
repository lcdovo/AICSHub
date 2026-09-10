"""RAG 检索降级测试：空结果/低分结果应进入"无可靠答案"分支，不硬答。"""
from app.agent.graph import AgentGraph
from app.agent.state import AgentState, AgentNode


class FakeKnowledgeBase:
    """假知识库：返回预设检索结果，不依赖 Milvus/内存 BM25。"""

    def __init__(self, results):
        self._results = results

    async def async_search(self, query, top_k):
        return {
            "success": True,
            "results": self._results,
            "meta": {"backend": "memory", "degraded": False},
            "execution_time_ms": 5,
        }


def _make_graph():
    return object.__new__(AgentGraph)


async def _run_retrieval(monkeypatch, results):
    monkeypatch.setattr("app.agent.graph.get_knowledge_base", lambda: FakeKnowledgeBase(results))
    graph = _make_graph()
    state = AgentState(session_id="s1", user_id=1, user_message="退货政策是什么")
    next_node = await graph._node_rag_retrieval(state)
    return next_node, state


async def test_rag_empty_results_falls_back(monkeypatch):
    next_node, state = await _run_retrieval(monkeypatch, [])
    assert next_node == AgentNode.RESPONSE_GENERATION
    assert state.collected_info.get("rag_grounded") is False
    assert state.collected_info.get("rag_fallback_reason") == "no_results"
    assert "知识库" in state.reply


async def test_rag_low_score_falls_back(monkeypatch):
    next_node, state = await _run_retrieval(
        monkeypatch, [{"title": "退货政策", "content": "支持7天无理由退货", "final_score": 0.1}]
    )
    assert next_node == AgentNode.RESPONSE_GENERATION
    assert state.collected_info.get("rag_grounded") is False
    assert state.collected_info.get("rag_fallback_reason") == "low_score"


async def test_rag_high_score_is_grounded(monkeypatch):
    next_node, state = await _run_retrieval(
        monkeypatch, [{"title": "退货政策", "content": "支持7天无理由退货", "final_score": 0.8}]
    )
    assert next_node == AgentNode.RESPONSE_GENERATION
    assert state.collected_info.get("rag_grounded") is True
    assert state.collected_info["kb_results"][0]["title"] == "退货政策"
