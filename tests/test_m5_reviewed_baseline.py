import copy
import hashlib
import json
import shutil

import pytest

from scripts.evaluate_m5_reviewed_bm25 import lexical_baseline, validate_review


def materials():
    draft = {"schema_version": 1, "split": "holdout", "label_source": "assistant_draft", "reviewer": None,
             "cases": [{"case_id": "q1", "cluster_id": "c1", "query": "合成耳机保修期限",
                        "kind": "answerable", "relevant": [{"chunk_id": "a", "version_id": "v1", "grade": 3}],
                        "required_conditions": [], "expected_support": "supported"}]}
    reviewed = {**copy.deepcopy(draft), "label_source": "human_reviewed", "reviewer": "user"}
    corpus = [{"chunk_id": "a", "version_id": "v1", "content": "合成耳机保修期限12个月"}]
    decisions = [{"reviewer": "user", "reviewed_at": "2026-11-05", "user_statement": "q1通过",
                  "dataset_sha256": "a" * 64, "corpus_sha256": "b" * 64,
                  "decisions": [{"case_id": "q1", "decision": "approved", "kind": "answerable"}]}]
    return draft, reviewed, corpus, decisions


@pytest.mark.parametrize("bad", ["label", "reviewer", "hash", "evidence_version", "duplicate", "missing", "kind", "unknown"])
def test_unapproved_or_changed_materials_cannot_be_scored_as_reviewed(bad):
    draft, reviewed, corpus, decisions = materials()
    if bad == "label": reviewed["cases"][0]["required_conditions"] = ["新增条件"]
    elif bad == "reviewer": reviewed["reviewer"] = "different"
    elif bad == "hash": decisions[0]["dataset_sha256"] = "c" * 64
    elif bad == "evidence_version": corpus[0]["version_id"] = "v2"
    elif bad == "duplicate": decisions *= 2
    elif bad == "missing": decisions[0]["decisions"] = []
    elif bad == "kind": decisions[0]["decisions"][0]["kind"] = "review_positive"
    else: decisions[0]["decisions"][0]["case_id"] = "unapproved"
    with pytest.raises(ValueError):
        validate_review(draft, reviewed, corpus, decisions, "a" * 64, "b" * 64)


def test_review_audit_only_confirms_material_consistency():
    result = validate_review(*materials(), "a" * 64, "b" * 64)
    assert result["approved_count"] == 1
    assert not result["whole_dataset_approved"] and not result["global_semantic_independence_verified"]


def test_missing_lexical_evidence_stays_in_metric_denominator():
    _, reviewed, corpus, _ = materials()
    corpus[0]["content"] = "abcdefgh"
    result = lexical_baseline(reviewed, corpus)
    assert result["evaluated_answerable_cases"] == 1
    assert result["metrics"]["recall_at_20"] == 0
    assert result["metrics"]["ndcg_at_5"] == 0


def test_lexical_hit_does_not_prove_generated_answer_quality():
    _, reviewed, corpus, _ = materials()
    result = lexical_baseline(reviewed, corpus)
    assert result["metrics"]["recall_at_20"] == 1
    assert result["model_calls"] == result["generated_answers"] == 0
    assert "claim_support" in result["not_measured"] and not result["whole_m5_pass"]


def test_real_report_entry_uses_portable_material_keys_and_never_claims_full_acceptance(tmp_path, monkeypatch):
    from scripts import evaluate_m5_reviewed_bm25 as module
    draft, reviewed, corpus, decisions = materials()
    docs = tmp_path / "docs"
    (docs / "m5-review").mkdir(parents=True)
    (tmp_path / "acceptance/m5").mkdir(parents=True)
    scorer = tmp_path / "app/services/knowledge_base/bm25.py"
    scorer.parent.mkdir(parents=True)
    shutil.copyfile(module.ROOT / "app/services/knowledge_base/bm25.py", scorer)
    for name, value in [("M5-HOLDOUT-DRAFT-v1.json", draft), ("M5-HOLDOUT-CORPUS-DRAFT-v1.json", corpus),
                        ("M5-HOLDOUT-REVIEWED-PARTIAL-v1.json", reviewed)]:
        (docs / name).write_text(json.dumps(value), encoding="utf-8")
    decisions[0]["dataset_sha256"] = hashlib.sha256((docs / "M5-HOLDOUT-DRAFT-v1.json").read_bytes()).hexdigest()
    decisions[0]["corpus_sha256"] = hashlib.sha256((docs / "M5-HOLDOUT-CORPUS-DRAFT-v1.json").read_bytes()).hexdigest()
    (docs / "m5-review/decision.json").write_text(json.dumps(decisions[0]), encoding="utf-8")
    (docs / "M5-GATES-v1.json").write_text(json.dumps({"human_review": {
        "decision_records": ["docs/m5-review/decision.json"], "approved_case_ids": ["q1"]}}), encoding="utf-8")
    development = copy.deepcopy(draft)
    development["split"] = "development"
    development["cases"][0].update(case_id="dev", cluster_id="dev-cluster", query="其他开发问题")
    (tmp_path / "acceptance/m5/development.json").write_text(json.dumps(development), encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr("scripts.test_provenance.snapshot_sources", lambda _: {})
    monkeypatch.setattr("scripts.test_provenance.compare_sources", lambda *_: {
        "source_tree_stable": True, "source_snapshot_complete": True})
    assert module.main() == 0
    report = json.loads(next((tmp_path / "reports").rglob("report.json")).read_bytes())
    assert report["input_materials_stable"]
    assert "docs/M5-HOLDOUT-DRAFT-v1.json" in report["input_hashes"]
    assert not report["whole_m5_pass"] and report["baseline"]["model_calls"] == 0
