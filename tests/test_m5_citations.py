import pytest
from types import SimpleNamespace
from app.agent.citation_guard import citation_issues
from app.agent.nodes.response_node import response_generation_node
from app.agent.request_understanding import EvidenceBundle
from app.agent.state import AgentNode
from tests.test_chat_turns import graph_for_response, rag_state


def test_citation_identity_does_not_append_unreviewed_other_order_status():
    from app.agent.critical_facts import critical_fact_issues
    graph = graph_for_response()
    evidence = [{"id": "policy-chunk", "version_id": "policy-version", "title": "退款流程",
                 "content": "未发货订单按另一套取消流程处理。"}]
    body = "订单ORD960001已签收。"
    tools = [{"success": True, "data": {"order_id": "ORD960001", "status_code": "delivered",
                                          "total_amount": "0.10", "currency": "CNY"}}]
    reply = graph._add_rag_citations(body, evidence)
    assert "未发货" not in reply and "[证据:policy-chunk@policy-version]" in reply
    assert not citation_issues(reply, evidence) and not critical_fact_issues(reply, tools)


@pytest.mark.parametrize("reference", ["missing@v1", "c1@obsolete", "c1", "c2@v1"])
async def test_fake_or_wrong_version_citation_is_rejected_even_when_judge_passes(reference,monkeypatch):
    monkeypatch.setattr("app.services.knowledge_base.get_knowledge_base",lambda: SimpleNamespace(evidence_is_current=lambda _:True))
    graph=graph_for_response()
    state=rag_state()
    state.evidence_bundle=EvidenceBundle(items=[{"id":"c1","version_id":"v1","content":"合成退货条件"}])
    state.reply=f"可按政策退货[证据:{reference}]。"
    assert await response_generation_node(graph,state)==AgentNode.END
    from app.agent.publication_guard import SAFE_PUBLICATION_REPLY
    assert state.reply == SAFE_PUBLICATION_REPLY and not state.review_result.passed
    assert "invalid_citation_identity" in state.collected_info["publication_guard"]["issues"]
    assert not graph._answer_cache.values


def test_valid_id_version_is_only_identity_check_not_support_proof():
    assert citation_issues("[证据:c1@v1]",[{"id":"c1","version_id":"v1","content":"合成证据"}])==[]
    assert citation_issues("[证据:c1@v1]",[])==["invalid_citation_identity"]


async def test_valid_reference_cannot_override_independent_judge_rejection(monkeypatch):
    monkeypatch.setattr("app.services.knowledge_base.get_knowledge_base",lambda: SimpleNamespace(evidence_is_current=lambda _:True))
    graph=graph_for_response(refuse=True)
    state=rag_state()
    state.evidence_bundle=EvidenceBundle(items=[{"id":"c1","version_id":"v1","content":"定制商品除外"}])
    state.reply="定制商品允许无理由退货[证据:c1@v1]。"
    assert await response_generation_node(graph,state)==AgentNode.END
    assert not state.review_result.passed and "足够可靠" in state.reply
