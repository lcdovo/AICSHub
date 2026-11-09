from copy import deepcopy
import json

import pytest

from app.agent.judge_contracts import assess_v2, evidence_catalog, response_segments


EVIDENCE = [{"id": "policy", "version_id": "v1", "content": "合成设备仅适用于允许机洗的织物。"}]


def verdict(response="合成设备仅适用于允许机洗的织物。", **changes):
    raw = {"schema_version": 2, "answerability": "full", "response_behavior": "answer", "response_validity": "valid",
        "claims_complete": True, "claims": [{"segment_id": 0, "text": response, "support": "supported", "issues": [],
            "evidence": [{"kind": "knowledge", "source_id": "policy", "version_id": "v1", "quote": EVIDENCE[0]["content"]}]}],
        "nonfact_segments": [], "response_issues": []}
    raw.update(changes)
    return raw


@pytest.mark.parametrize("behavior", ["clarify", "refuse_insufficient", "refuse_action", "advice"])
def test_unknown_question_can_have_a_valid_safe_response(behavior):
    response = "资料不足，请提供具体型号。"
    raw = verdict(answerability="unknown", response_behavior=behavior, claims=[],
                  nonfact_segments=[{"segment_id": 0, "kind": "clarification"}])
    result = assess_v2(raw, response, EVIDENCE)
    assert result["publish_allowed"] and result["support"] == "supported"
    assert result["evidence_support"] == "unknown" and not result["cacheable"]


@pytest.mark.parametrize("issue", ["negation_changed", "scope_expanded", "condition_missing", "exception_missing", "unsupported_detail"])
def test_supported_self_label_cannot_override_explicit_scope_or_condition_issue(issue):
    raw = verdict()
    raw["claims"][0]["issues"] = [issue]
    assert not assess_v2(raw, EVIDENCE[0]["content"], EVIDENCE)["publish_allowed"]


@pytest.mark.parametrize("corruption", ["version", "source", "quote", "candidate", "missing_reference", "incomplete", "unsupported", "unknown"])
def test_claim_provenance_and_coverage_are_required(corruption):
    raw = verdict()
    if corruption == "version": raw["claims"][0]["evidence"][0]["version_id"] = "old"
    elif corruption == "source": raw["claims"][0]["evidence"][0]["source_id"] = "other"
    elif corruption == "quote": raw["claims"][0]["evidence"][0]["quote"] = "编造条款"
    elif corruption == "candidate": raw["claims"][0]["text"] = "未在候选中出现"
    elif corruption == "missing_reference": raw["claims"][0]["evidence"] = []
    elif corruption == "incomplete": raw["claims_complete"] = False
    else: raw["claims"][0]["support"] = corruption
    try:
        result = assess_v2(raw, EVIDENCE[0]["content"], EVIDENCE)
    except ValueError:
        return
    assert not result["publish_allowed"]


def test_clarification_cannot_hide_an_extra_unsupported_fact():
    response = "资料不足。此型号支持999W。"
    raw = verdict(response_behavior="clarify", answerability="unknown")
    raw["nonfact_segments"] = [{"segment_id": 0, "kind": "insufficient_notice"}]
    raw["claims"] = [{"segment_id": 1, "text": "此型号支持999W。", "support": "unsupported", "evidence": [], "issues": ["unsupported_detail"]}]
    assert not assess_v2(raw, response, EVIDENCE)["publish_allowed"]


@pytest.mark.parametrize("change", ["unreviewed_sentence", "duplicate_nonfact", "fact_also_nonfact", "missing_claims"])
def test_segment_coverage_and_role_consistency(change):
    response = EVIDENCE[0]["content"]
    raw = verdict()
    if change == "unreviewed_sentence": response += "另可终身换新。"
    elif change == "duplicate_nonfact": raw["nonfact_segments"] = [{"segment_id": 1, "kind": "social"}] * 2
    elif change == "fact_also_nonfact": raw["nonfact_segments"] = [{"segment_id": 0, "kind": "social"}]
    else: raw.update(claims=[], nonfact_segments=[{"segment_id": 0, "kind": "social"}])
    with pytest.raises(ValueError):
        assess_v2(raw, response, EVIDENCE)


def test_safe_partial_answer_can_be_sent_without_full_answer_cache_admission():
    response = EVIDENCE[0]["content"] + "请提供衣标。"
    raw = verdict(answerability="partial", response_behavior="answer_with_limits",
                  nonfact_segments=[{"segment_id": 1, "kind": "clarification"}])
    result = assess_v2(raw, response, EVIDENCE)
    assert result["publish_allowed"] and result["evidence_support"] == "partial" and not result["cacheable"]
    raw["response_behavior"] = "answer"
    assert not assess_v2(raw, response, EVIDENCE)["publish_allowed"]


def test_tool_evidence_identity_is_bound_to_current_receipt_content():
    tool = json.dumps({"refund_id": "synthetic", "status": "pending_review", "is_simulated": True}, ensure_ascii=False)
    catalog = evidence_catalog([], tool)
    key, body = next(iter(catalog.items()))
    raw = verdict("模拟申请待审核。")
    raw["claims"][0]["evidence"] = [{"kind": key[0], "source_id": key[1], "version_id": key[2], "quote": body}]
    assert assess_v2(raw, "模拟申请待审核。", [], tool)["publish_allowed"]
    with pytest.raises(ValueError):
        assess_v2(raw, "模拟申请待审核。", [], tool.replace("pending_review", "rejected"))


def test_normalized_metadata_does_not_persist_candidate_or_evidence_quotes():
    raw = verdict()
    result = assess_v2(raw, EVIDENCE[0]["content"], EVIDENCE)
    assert EVIDENCE[0]["content"] not in str(result)
    assert result["claim_checks"][0]["references_verified"]


def test_unknown_stays_unknown_and_duplicate_evidence_cannot_be_ambiguous():
    raw = verdict(response_validity="unknown")
    assert assess_v2(raw, EVIDENCE[0]["content"], EVIDENCE)["support"] == "unknown"
    with pytest.raises(ValueError):
        evidence_catalog(EVIDENCE + deepcopy(EVIDENCE))
    assert response_segments(" 一句。\n\n二句？ ") == [{"segment_id": 0, "text": "一句。"}, {"segment_id": 1, "text": "二句？"}]


@pytest.mark.parametrize("kind,code", [
    ("version", "evidence_identity_or_version_invalid"),
    ("quote", "evidence_quote_not_in_current_source"),
    ("candidate", "claim_not_in_current_candidate"),
    ("coverage", "candidate_segments_not_exactly_reviewed"),
])
def test_binding_failures_have_stable_codes_without_echoing_model_text(kind, code):
    from app.agent.validation_llm import assess_judgement
    raw = verdict()
    if kind == "version": raw["claims"][0]["evidence"][0]["version_id"] = "wrong"
    elif kind == "quote": raw["claims"][0]["evidence"][0]["quote"] = "synthetic-private-model-quote"
    elif kind == "candidate": raw["claims"][0]["text"] = "synthetic-private-model-text"
    else: raw["nonfact_segments"] = [{"segment_id": 7, "kind": "social"}]
    result = assess_judgement(raw, response=EVIDENCE[0]["content"], kb_context=EVIDENCE, contract_version="v2")
    assert result["contract_failure_code"] == code and not result["publish_allowed"]
    assert "synthetic-private" not in str(result)
