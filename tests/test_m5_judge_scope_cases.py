"""已见真实误放的契约反例；判决为手工替身，不冒充模型已学会识别。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.agent.judge_contracts import assess_v2


ROOT = Path(__file__).resolve().parents[1]
CONFIRMED_IDS = ["holdout-negative-policy-006-2", "holdout-negative-policy-014-1",
                 "holdout-negative-policy-024-1", "holdout-negative-policy-027-2", "holdout-negative-policy-038-2"]


def scope_fixture(case_id: str) -> tuple:
    dataset = json.loads((ROOT / "acceptance/m5/adjudicated-v2/dataset.json").read_bytes())
    corpus = json.loads((ROOT / "acceptance/m5/adjudicated-v2/corpus.json").read_bytes())
    case = next(item for item in dataset["cases"] if item["case_id"] == case_id)
    ref = case["relevant"][0]
    doc = next(item for item in corpus if item["chunk_id"] == ref["chunk_id"] and item["version_id"] == ref["version_id"])
    candidate = case["candidate"]
    original = candidate.replace("不只", "仅")
    assert original in doc["content"]
    evidence = [{"id": ref["chunk_id"], "version_id": ref["version_id"], "content": doc["content"]}]
    raw = {"schema_version": 2, "answerability": "full", "response_behavior": "answer", "response_validity": "valid",
        "claims_complete": True, "nonfact_segments": [], "response_issues": [],
        "claims": [{"segment_id": 0, "text": candidate, "support": "supported", "issues": ["scope_expanded"],
            "evidence": [{"kind": "knowledge", "source_id": ref["chunk_id"], "version_id": ref["version_id"], "quote": original}]}]}
    return candidate, original, raw, evidence


@pytest.mark.parametrize("case_id", CONFIRMED_IDS)
def test_explicit_scope_issue_vetoes_supported_label_despite_existing_reference(case_id):
    candidate, _, raw, evidence = scope_fixture(case_id)
    result = assess_v2(raw, candidate, evidence)
    assert not result["publish_allowed"] and result["claim_checks"][0]["references_verified"]


@pytest.mark.parametrize("case_id", CONFIRMED_IDS)
def test_original_only_scope_counterpart_is_not_rejected_by_word_alone(case_id):
    _, original, raw, evidence = scope_fixture(case_id)
    raw["claims"][0].update(text=original, issues=[])
    assert assess_v2(raw, original, evidence)["publish_allowed"]


@pytest.mark.parametrize("case_id", CONFIRMED_IDS)
def test_semantic_detection_remains_unverified_when_model_omits_scope_issue(case_id):
    candidate, _, raw, evidence = scope_fixture(case_id)
    raw = deepcopy(raw)
    raw["claims"][0]["issues"] = []
    # 引用/逐句契约不是语义证明；不得把这些手工判决测试写成真实模型0误放。
    assert assess_v2(raw, candidate, evidence)["publish_allowed"]
