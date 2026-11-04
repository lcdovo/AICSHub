import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.validation_llm import LLMRAGJudge
from app.agent.nodes.response_node import response_generation_node
from app.agent.state import AgentNode
from tests.test_chat_turns import graph_for_response, rag_state


def test_stricter_review_uses_new_cache_namespace():
    assert graph_for_response()._build_answer_cache_key(rag_state()).startswith("validated-v3:")


@pytest.mark.parametrize("failure", ["disabled", "unavailable", "none", "invalid", "timeout", "exception"])
async def test_judge_failure_is_explicit_unknown(monkeypatch, failure):
    monkeypatch.setattr("app.agent.validation_llm.settings.LLM_JUDGE_ENABLED", failure != "disabled")
    async def structured(*args, **kwargs):
        if failure == "timeout":
            await asyncio.sleep(1)
        if failure == "exception":
            raise RuntimeError("synthetic-private-error")
        if failure == "invalid":
            return {"grounded": "true", "fact_score": 1.0}
        return None
    model = SimpleNamespace(is_available=lambda: failure != "unavailable", run_structured=AsyncMock(side_effect=structured))
    judge = LLMRAGJudge(model)
    monkeypatch.setattr(judge, "TIMEOUT_SECONDS", .01, raising=False)
    outcome = await judge.judge("合成政策回答", "合成问题", [{"content": "合成依据"}])
    assert outcome and outcome["support"] == "unknown" and outcome["refuse_recommended"]
    assert outcome["fact_score"] is None and "synthetic-private-error" not in str(outcome)


@pytest.mark.parametrize("verdict", [None, {}, {"grounded": "true", "fact_score": .99},
    {"grounded": False, "fact_score": .99, "has_unsupported_claim": False, "refuse_recommended": False},
    {"grounded": True, "fact_score": float("nan"), "has_unsupported_claim": False, "refuse_recommended": False},
])
async def test_missing_or_ungrounded_judge_never_publishes_policy_or_caches(verdict):
    graph = graph_for_response()
    graph.llm_judge.judge = AsyncMock(return_value=verdict)
    state = rag_state()
    assert await response_generation_node(graph, state) == AgentNode.END
    assert not state.review_result.passed and not graph._answer_cache.values
    assert "足够可靠" in state.reply
    assert state.collected_info["llm_judge"]["support"] in {"unknown", "unsupported", "partial"}


async def test_generation_and_judge_see_same_full_evidence_and_tool_receipt(monkeypatch):
    monkeypatch.setattr("app.agent.validation_llm.settings.LLM_JUDGE_ENABLED", True)
    graph = graph_for_response()
    state = rag_state()
    tail = "定制商品不适用七天无理由规则"
    chunks = [{"id": f"c{i}", "version_id": f"v{i}", "title": "合成规则", "content": "普通条件" * 100 + tail} for i in range(5)]
    from app.agent.request_understanding import EvidenceBundle
    state.evidence_bundle = EvidenceBundle(items=chunks)
    tool = {"order_id": "SYNTHETIC", "note": "合成说明" * 150, "exception": tail}
    state.add_tool_call("query_order", {}, {"success": True, "data": tool}, True)
    prompt = graph._build_llm_prompt(state)
    formatted = LLMRAGJudge._format_evidence(state.current_evidence(), graph._tool_evidence_text(state))
    assert formatted in prompt and "c4" in formatted and formatted.count(tail) == 6
    assert json.dumps(tool, ensure_ascii=False) in formatted
