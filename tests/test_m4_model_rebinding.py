from types import SimpleNamespace
from unittest.mock import AsyncMock
from app.agent.graph import AgentGraph
from app.agent.validation_llm import LLMRAGJudge


async def test_replacing_graph_model_rebinds_owned_rewriter_and_judge(monkeypatch):
    monkeypatch.setattr("app.agent.query_rewrite_llm.settings.LLM_QUERY_REWRITE_ENABLED", True)
    monkeypatch.setattr("app.agent.validation_llm.settings.LLM_JUDGE_ENABLED", True)
    graph = AgentGraph()
    previous = graph.llm_service
    old_calls = AsyncMock(side_effect=AssertionError("old supplier service reached"))
    monkeypatch.setattr(previous, "run_structured", old_calls)

    async def structured(prompt, text, schema, **kwargs):
        if "rewritten_query" in schema.model_fields:
            return schema(rewritten_query="耳机保修期限", applied=True, reason="synthetic", confidence=.9)
        return schema(grounded=True, fact_score=1.0, has_unsupported_claim=False,
                      refuse_recommended=False, reason="synthetic")

    replacement = SimpleNamespace(is_available=lambda: True, run_structured=AsyncMock(side_effect=structured))
    try:
        graph.llm_service = replacement
        assert graph.llm_rewriter.llm_service is replacement
        assert graph.llm_judge.llm_service is replacement
        rewritten = await graph.llm_rewriter._rewrite_with_llm(
            "它能保修多久", [{"role": "user", "content": "耳机"}], {})
        assert rewritten and rewritten.rewritten_query == "耳机保修期限"
        verdict = await graph.llm_judge.judge(
            response="合成回答", user_query="耳机保修多久", kb_context=[{"content": "合成保修依据"}], tool_evidence="")
        assert verdict and verdict["grounded"]
        assert replacement.run_structured.await_count == 2
        old_calls.assert_not_awaited()
    finally:
        await previous.close()


async def test_explicit_independent_judge_model_is_not_silently_replaced():
    graph = AgentGraph()
    previous = graph.llm_service
    dedicated = SimpleNamespace(is_available=lambda: False)
    graph.llm_judge = LLMRAGJudge(dedicated)
    replacement = SimpleNamespace(is_available=lambda: False)
    try:
        graph.llm_service = replacement
        assert graph.llm_rewriter.llm_service is replacement
        assert graph.llm_judge.llm_service is dedicated
    finally:
        await previous.close()
