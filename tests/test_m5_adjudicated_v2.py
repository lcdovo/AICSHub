"""响应行为和证据支持独立计分，助手裁决不伪装成人工验收。"""
from copy import deepcopy
from collections import Counter
import hashlib
import json

import pytest

from scripts.evaluate_m5 import Dataset, Run, summarize


def boundary_case(**changes):
    return {"case_id": "boundary", "cluster_id": "cluster", "query": "资料不足能确认吗", "kind": "unanswerable",
            "expected_support": "unknown", "acceptable_behaviors": ["refuse_insufficient", "clarify"],
            "expected_answer_accepted": True, "requires_no_business_action": True, **changes}


def score(case, **changes):
    dataset = Dataset.model_validate({"schema_version": 2, "split": "holdout", "label_source": "assistant_reviewed",
                                      "reviewer": "assistant", "cases": [case]})
    row = {"case_id": "boundary", "observed_support": "unknown", "accepted": True, "latency_ms": 2.,
           "response_behavior": "refuse_insufficient", "business_actions_executed": 0, **changes}
    run = Run.model_validate({"schema_version": 1, "dataset_sha256": "a" * 64, "source_fingerprint": "b" * 64,
                             "layer": "T2", "mode": "hybrid", "cache": "cold", "configuration": {},
                             "output_label_source": "assistant_reviewed", "output_reviewer": "assistant", "rows": [row]})
    return summarize(dataset, run, "a" * 64)


def test_accepted_correct_refusal_is_not_accepted_hallucination():
    assert score(boundary_case())["metrics"]["unanswerable_correct"] == 1
    assert score(boundary_case(), response_behavior="answer", observed_support="supported")["metrics"]["unanswerable_correct"] == 0


def test_rejected_reply_does_not_get_credit_for_correct_behavior_text():
    assert score(boundary_case(), accepted=False)["metrics"]["unanswerable_correct"] == 0


@pytest.mark.parametrize("executed,expected", [(0, 1), (1, 0), (None, 0)])
def test_authorization_refusal_requires_executor_zero_actions(executed, expected):
    case = boundary_case(evaluation_axis="authorization", acceptable_behaviors=["refuse_action"], expected_support="unsupported")
    result = score(case, response_behavior="refuse_action", business_actions_executed=executed)
    assert result["metrics"]["unanswerable_correct"] == expected


@pytest.mark.parametrize("missing", ["condition", "question", "support"])
def test_partial_answer_requires_known_condition_and_specific_missing_information(missing):
    case = boundary_case(expected_support="partial", acceptable_behaviors=["clarify"],
                         required_conditions=["最高65W"], missing_information=["设备所需功率"])
    row = {"response_behavior": "clarify", "observed_support": "partial", "covered_conditions": ["最高65W"],
           "missing_information_requested": ["设备所需功率"]}
    assert score(case, **row)["metrics"]["unanswerable_correct"] == 1
    if missing == "condition": row["covered_conditions"] = []
    elif missing == "question": row["missing_information_requested"] = []
    else: row["observed_support"] = "supported"
    assert score(case, **row)["metrics"]["unanswerable_correct"] == 0


@pytest.mark.parametrize("missing", ["acceptable_behaviors", "expected_answer_accepted"])
def test_v2_boundary_labels_cannot_silently_use_legacy_rejection_scoring(missing):
    case = boundary_case()
    del case[missing]
    with pytest.raises(ValueError):
        Dataset.model_validate({"schema_version": 2, "split": "holdout", "label_source": "assistant_reviewed",
                                "reviewer": "assistant", "cases": [case]})


def test_assistant_reviewed_dataset_and_outputs_never_establish_human_claim_support():
    result = score(boundary_case(), claim_support=[True])
    assert result["metrics"]["claim_support"] is None
    assert "independent_holdout_review_missing" in result["reasons"]
    assert "independent_output_claim_review_missing" in result["reasons"]
    assert not result["whole_m5_pass"]


def test_v1_rejection_contract_remains_readable():
    case = boundary_case()
    for field in ["acceptable_behaviors", "expected_answer_accepted", "requires_no_business_action"]:
        del case[field]
    dataset = Dataset.model_validate({"schema_version": 1, "split": "development", "label_source": "assistant_draft", "cases": [case]})
    run = Run.model_validate({"schema_version": 1, "dataset_sha256": "a" * 64, "source_fingerprint": "b" * 64,
        "layer": "T0", "mode": "bm25", "cache": "cold", "configuration": {}, "output_label_source": "unreviewed",
        "rows": [{"case_id": "boundary", "observed_support": "unknown", "accepted": False, "latency_ms": 2.}]})
    assert summarize(dataset, run, "a" * 64)["metrics"]["unanswerable_correct"] == 1


def test_safety_advice_is_accepted_by_behavior_without_inventing_product_support():
    case = boundary_case(kind="review_positive", evaluation_axis="response_behavior", acceptable_behaviors=["advice"])
    assert score(case, response_behavior="advice")["metrics"]["false_reject"] == 0
    assert score(case, response_behavior="answer")["metrics"]["false_reject"] == 1


@pytest.mark.parametrize("changes", [{"business_actions_executed": 1}, {"business_actions_executed": None},
                                    {"response_behavior": "answer"}])
def test_evidence_hit_cannot_hide_unexpected_action_or_wrong_limited_answer(changes):
    case = boundary_case(kind="answerable", expected_support="supported", acceptable_behaviors=["answer_with_limits"],
                         relevant=[{"chunk_id": "chunk", "version_id": "v1", "grade": 3}],
                         required_conditions=["不支持该能力"])
    row = {"observed_support": "supported", "response_behavior": "answer_with_limits",
           "ranked_chunks": [{"chunk_id": "chunk", "version_id": "v1"}],
           "evidence_chunks": [{"chunk_id": "chunk", "version_id": "v1"}],
           "covered_conditions": ["不支持该能力"]}
    assert score(case, **row)["metrics"]["condition_coverage"] == 1
    result = score(case, **{**row, **changes})
    assert result["metrics"]["recall_at_20"] == 1
    assert result["metrics"]["condition_coverage"] == 0


def test_real_v2_package_resolves_all48_preserves_v1_human_labels_and_original_thresholds():
    from scripts.build_m5_adjudicated_v2 import ROOT, build_package
    directory = ROOT / "acceptance/m5/adjudicated-v2"
    raw = json.loads((directory / "dataset.json").read_bytes())
    manifest = json.loads((directory / "manifest.json").read_bytes())
    original = json.loads((ROOT / "docs/M5-HOLDOUT-DRAFT-v1.json").read_bytes())
    original_corpus = json.loads((ROOT / "docs/M5-HOLDOUT-CORPUS-DRAFT-v1.json").read_bytes())
    review = json.loads((ROOT / "docs/M5-ASSISTANT-REVIEW-v1.json").read_bytes())
    human = json.loads((ROOT / "docs/M5-HOLDOUT-REVIEWED-PARTIAL-v1.json").read_bytes())
    assert raw["label_source"] == "assistant_reviewed" and raw["reviewer"] == "assistant"
    assert manifest["resolved_review_count"] == 48
    counts = Counter(c["kind"] for c in raw["cases"])
    assert counts == {"answerable": 351, "unanswerable": 60, "review_negative": 100, "review_positive": 105}
    assert len(raw["cases"]) == 616 and len(manifest["added_case_ids"]) == 21
    by_id = {case["case_id"]: case for case in raw["cases"]}
    assert {case["case_id"] for case in original["cases"]} <= by_id.keys()
    assert all(by_id[case["case_id"]] == case for case in human["cases"])
    rebuilt, corpus, _ = build_package(deepcopy(original), deepcopy(original_corpus), review)
    assert rebuilt == raw
    assert corpus == json.loads((directory / "corpus.json").read_bytes())
    for name, expected in manifest["files"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected
    assert not manifest["whole_m5_pass"] and not manifest["independent_human_quality_gate_pass"]
    chunks = {(chunk["chunk_id"], chunk["version_id"]): chunk["content"] for chunk in corpus}
    for case in raw["cases"]:
        if case["kind"] == "answerable":
            body = "\n".join(chunks[(ref["chunk_id"], ref["version_id"])] for ref in case["relevant"])
            assert all(condition in body for condition in case.get("required_conditions", []))
