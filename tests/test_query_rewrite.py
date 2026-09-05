import asyncio

from app.agent.graph import AgentGraph
from app.agent.query_rewrite import ConservativeQueryRewriter
from app.agent.state import AgentState, IntentType


def test_rewriter_expands_reference_with_previous_user_query():
    result = ConservativeQueryRewriter().rewrite(
        "这个怎么退？",
        messages=[
            {"role": "user", "content": "商品 ORD20260801 有质量问题"},
            {"role": "assistant", "content": "我可以帮您处理售后。"},
            {"role": "user", "content": "这个怎么退？"},
        ],
    )

    assert result.applied is True
    assert result.original_query == "这个怎么退？"
    assert "商品 ORD20260801 有质量问题" in result.rewritten_query
    assert "这个怎么退？" in result.rewritten_query


def test_rewriter_keeps_self_contained_query_unchanged():
    result = ConservativeQueryRewriter().rewrite(
        "如何设置双因素认证？",
        messages=[{"role": "user", "content": "如何修改登录密码？"}],
    )

    assert result.applied is False
    assert result.rewritten_query == result.original_query


def test_query_rewrite_node_only_changes_rag_query(monkeypatch):
    captured = {}

    class KnowledgeBase:
        async def async_search(self, **kwargs):
            captured.update(kwargs)
            return {"success": True, "results": [], "meta": {}}

    monkeypatch.setattr("app.agent.graph.get_knowledge_base", lambda: KnowledgeBase())

    async def scenario():
        graph = AgentGraph()
        state = AgentState(
            session_id="rewrite-node",
            user_id=1,
            user_message="这个怎么设置？",
            detected_intent=IntentType.TECHNICAL,
            messages=[
                {"role": "user", "content": "蓝牙耳机无法连接"},
                {"role": "assistant", "content": "请描述具体问题。"},
                {"role": "user", "content": "这个怎么设置？"},
            ],
        )
        next_node = await graph._node_query_rewrite(state)
        assert next_node == "rag_retrieval"
        await graph._node_rag_retrieval(state)
        return state

    state = asyncio.run(scenario())
    assert state.user_message == "这个怎么设置？"
    assert state.query_rewrite_applied is True
    assert captured["query"] == state.rewritten_query
    assert captured["query"] != state.user_message


def test_rag_intents_route_through_query_rewrite_node():
    async def scenario():
        graph = AgentGraph()
        state = AgentState(
            session_id="rewrite-route",
            user_id=1,
            user_message="优惠活动还有吗？",
            detected_intent=IntentType.PROMOTION,
        )
        return await graph._node_route_decision(state)

    assert asyncio.run(scenario()) == "query_rewrite"
