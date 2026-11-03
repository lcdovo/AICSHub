from scripts.evaluate_m4_live import UntrustedPlanProposal, validate_probe_proposal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
import pytest


def test_probe_uses_actual_allowlist_and_forbids_forged_principal():
    unregistered = UntrustedPlanProposal(tool="exec", arguments={"resource_id": "synthetic"})
    assert validate_probe_proposal(unregistered) == {"rejected": True, "error_types": ["literal_error"]}
    forged = UntrustedPlanProposal(tool="query_order", arguments={"resource_id": "ORD950017", "user_id": 202})
    assert validate_probe_proposal(forged) == {"rejected": True, "error_types": ["extra_forbidden"]}


def test_safe_proposal_is_not_false_positive_pass_for_negative_probe():
    allowed = UntrustedPlanProposal(tool="query_order", arguments={"resource_id": "ORD950017"})
    assert validate_probe_proposal(allowed) == {"rejected": False, "error_types": []}


CASES = json.loads((Path(__file__).resolve().parents[1] / "acceptance/m4/unsafe-proposal-replay.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
async def test_real_supplier_proposal_replayed_through_production_understanding_graph_cannot_reach_tools(case):
    from pydantic import ValidationError
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState, AgentNode

    proposal = case["proposal"]
    raw = dict(topic="query_order", request_type="query",
               entities={"order_id": "ORD950017", "product": None}, conditions=[], subrequests=[],
               service_preference="unspecified", emotion="neutral", clarification=None,
               retrieval_query="查询订单ORD950017", entity_source="current_message",
               read_tasks=[{**proposal["arguments"], "tool": proposal["tool"]}])
    rejected = []

    async def replay(system, text, contract, **kwargs):
        try:
            return contract.model_validate(raw)
        except ValidationError:
            rejected.append(True)
            return None

    graph = AgentGraph()
    original = graph.llm_service
    graph.llm_service = SimpleNamespace(is_available=lambda: True, run_structured=AsyncMock(side_effect=replay))
    tool = AsyncMock(side_effect=AssertionError("rejected proposal reached tool"))
    handoff = AsyncMock(side_effect=AssertionError("rejected proposal reached handoff write"))
    graph.nodes[AgentNode.TOOL_EXECUTION] = tool
    graph.nodes[AgentNode.HUMAN_HANDOFF] = handoff
    graph.checkpointer = InMemorySaver()
    graph.langgraph = graph._build_langgraph()
    try:
        state = await graph.run(AgentState(session_id="synthetic-replay", user_id=101,
                                           user_message="查询订单ORD950017", understanding_mode="model"))
        assert state.current_node == AgentNode.END and state.needs_clarification
        assert not state.tool_calls and not state.pending_confirmations
        assert len(rejected) == 2  # 至多一次修复仍无效，安全终止；本回放不发新HTTP请求。
        tool.assert_not_awaited()
        handoff.assert_not_awaited()
    finally:
        await original.close()
