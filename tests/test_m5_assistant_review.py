import copy

import pytest

from scripts.audit_m5_assistant_review import case_digest, validate_review


def materials():
    human_case = {"case_id": "human", "kind": "answerable", "query": "已人工批准"}
    other = {"case_id": "assistant", "kind": "unanswerable", "query": "后续复核"}
    dataset = {"label_source": "assistant_draft", "cases": [human_case, other]}
    human = {"label_source": "human_reviewed", "cases": [copy.deepcopy(human_case)]}
    corpus = [{"chunk_id": "chunk", "version_id": "v1", "content": "合成条款不提供额外能力"}]
    hashes = {"dataset_sha256": "a" * 64, "corpus_sha256": "b" * 64}
    review = {**hashes, "reviewer": "assistant", "independent_human_review": False,
              "whole_m5_pass": False, "external_model_calls": 0, "protected_human_case_ids": ["human"],
              "assistant_reviewed_count": 1, "human_reviewed_count": 1, "counts": {"needs_revision": 1},
              "records": [{"case_id": "assistant", "kind": "unanswerable", "case_sha256": case_digest(other),
                           "reviewer": "assistant", "independent_human_review": False,
                           "disposition": "needs_revision", "category": "behavior", "reason": "区分否定与拒答",
                           "proposed_revision": "补充预期行为", "evidence": [{"chunk_id": "chunk",
                           "version_id": "v1", "quote": "不提供额外能力"}]}]}
    return review, dataset, corpus, human, hashes


@pytest.mark.parametrize("bad", ["reviewer", "human_flag", "pass", "hash", "duplicate", "missing", "human_case",
                                "changed_human", "changed_case", "wrong_version", "fake_quote", "count", "no_proposal"])
def test_delegation_never_forges_human_review_or_changes_original_evidence(bad):
    review, dataset, corpus, human, hashes = materials()
    if bad == "reviewer": review["reviewer"] = "user"
    elif bad == "human_flag": review["records"][0]["independent_human_review"] = True
    elif bad == "pass": review["whole_m5_pass"] = True
    elif bad == "hash": hashes["dataset_sha256"] = "c" * 64
    elif bad == "duplicate": review["records"] *= 2
    elif bad == "missing": review["records"] = []
    elif bad == "human_case": review["records"][0]["case_id"] = "human"
    elif bad == "changed_human": human["cases"][0]["query"] = "篡改"
    elif bad == "changed_case": dataset["cases"][1]["query"] = "修改"
    elif bad == "wrong_version": review["records"][0]["evidence"][0]["version_id"] = "v2"
    elif bad == "fake_quote": review["records"][0]["evidence"][0]["quote"] = "从未出现的承诺"
    elif bad == "count": review["counts"] = {"consistent": 1}
    else: review["records"][0]["proposed_revision"] = None
    with pytest.raises(ValueError):
        validate_review(review, dataset, corpus, human, hashes)


def test_valid_assistant_review_only_passes_artifact_consistency():
    result = validate_review(*materials())
    assert result["assistant_reviewed_count"] == result["human_reviewed_count"] == 1
    assert not result["whole_m5_pass"] and not result["independent_human_quality_gate_pass"]
