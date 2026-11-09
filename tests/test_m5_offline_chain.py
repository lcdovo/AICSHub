"""生产组件诊断的计分/来源边界，不能用固定标签作Judge。"""
from copy import deepcopy
import hashlib
import json

import pytest

from scripts.evaluate_m5 import Dataset
from scripts.evaluate_m5_offline_chain import load_package, populate, retrieval_baseline, review_candidates
from tests.test_m3_lifecycle import kb as kb


def data(kind="answerable", **changes):
    case = {"case_id": "isolated", "cluster_id": "isolated-cluster", "query": "合成商品保修期限",
            "kind": kind, "relevant": [{"chunk_id": "canonical", "version_id": "v1", "grade": 3}],
            "expected_support": "supported", "required_conditions": ["合成商品保修12个月。"]}
    case.update(changes)
    return Dataset.model_validate({"schema_version": 2, "split": "holdout", "label_source": "assistant_reviewed",
                                   "reviewer": "assistant", "cases": [case]})


@pytest.fixture
def indexed(kb, monkeypatch):
    from app.config.config import settings
    monkeypatch.setattr(settings, "RAG_USE_RERANKER", False)
    corpus = [{"title": "合成评测", "chunk_id": "canonical", "version_id": "v1", "content": "合成商品保修12个月。"}]
    return kb, populate(kb, corpus)


def test_production_bm25_cold_hot_uses_sql_labels_without_query_model(indexed, monkeypatch):
    service, mapping = indexed
    def forbidden(_):
        pytest.fail("lexical query must not call embedding")
    monkeypatch.setattr(service._embedding_service, "encode_query", forbidden)
    report = retrieval_baseline(service, data(), mapping, evidence_k=5)
    assert [r["cache"] for r in report["rows"]] == ["cold", "hot"]
    assert all(r["recall_at_20"] == 1 and r["required_evidence_text_present"] for r in report["rows"])
    assert report["rows"][0]["ranked_chunks"][0]["chunk_id"] == "canonical"
    assert report["rows"][1]["metadata"]["timings_ms"]["encode"] == 0
    assert report["claim_support"] is report["condition_coverage"] is None


def test_retrieval_exception_retains_both_samples_and_scores_zero(indexed, monkeypatch):
    service, mapping = indexed
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(service, "search", fail)
    report = retrieval_baseline(service, data(), mapping, evidence_k=5)
    assert len(report["rows"]) == 2
    assert all(s["failed"] == 1 and s["recall_at_20"] == 0 for s in report["summaries"])


def test_evidence_text_presence_is_not_inferred_from_relevant_id(indexed):
    service, mapping = indexed
    report = retrieval_baseline(service, data(required_conditions=["证据中不存在的条件"]), mapping, evidence_k=5)
    assert all(r["recall_at_20"] == 1 and not r["required_evidence_text_present"] for r in report["rows"])


def test_multichunk_documents_are_not_silently_mapped_to_first_chunk(kb):
    with pytest.raises(ValueError, match="canonical_chunk_mapping_not_exact"):
        populate(kb, [{"title": "长文", "chunk_id": "one", "version_id": "v1", "content": "合成条件。" * 160}])


@pytest.mark.asyncio
async def test_expected_labels_do_not_determine_gate_verdict(indexed):
    service, mapping = indexed
    positive = data(kind="review_positive", candidate="合成商品保修12个月。")
    negative = data(kind="review_negative", candidate="合成商品保修12个月。", expected_support="unsupported", p0=True)
    first = (await review_candidates(positive, mapping, service))["rows"][0]
    second = (await review_candidates(negative, mapping, service))["rows"][0]
    for field in ("deterministic_accepted", "gate_accepted_without_judge", "issue_codes", "judge_support"):
        assert first[field] == second[field]
    assert first["deterministic_accepted"] and not first["gate_accepted_without_judge"]
    assert first["judge_support"] == "unknown"  # 正确候选被拒也不能当Judge质量通过。


@pytest.mark.asyncio
async def test_unrecognized_negative_remains_a_false_accept_in_component_diagnostic(indexed):
    service, mapping = indexed
    result = await review_candidates(data(kind="review_negative", candidate="合成商品保修999个月。", p0=True), mapping, service)
    assert result["deterministic_false_accept"] == 1
    assert result["p0_deterministic_false_accept_ids"] == ["isolated"]
    assert not result["unknown_judge_policy_accepted_ids"] and not result["whole_m5_pass"]


@pytest.mark.asyncio
async def test_missing_candidate_is_failure_not_successful_rejection(indexed):
    service, mapping = indexed
    result = await review_candidates(data(kind="review_negative", p0=True), mapping, service)
    assert result["failed"] == 1 and result["deterministic_false_accept"] == 1
    assert result["rows"][0]["error"] == "ValueError"


@pytest.mark.parametrize("corruption", ["hash", "version", "count", "split"])
def test_package_or_split_drift_is_rejected(tmp_path, corruption):
    raw = data().model_dump()
    corpus = [{"chunk_id": "canonical", "version_id": "v1", "content": "合成商品保修12个月。"}]
    development = deepcopy(raw)
    development["split"] = "development"
    development["cases"][0].update(case_id="dev", cluster_id="dev", query="不同开发输入")
    if corruption == "version": corpus[0]["version_id"] = "v2"
    if corruption == "split": development["cases"][0]["cluster_id"] = "isolated-cluster"
    for name, value in (("dataset.json", raw), ("corpus.json", corpus), ("development.json", development)):
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    manifest = {"total_cases": 9 if corruption == "count" else 1, "case_counts": {"answerable": 1},
                "files": {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in ("dataset.json", "corpus.json")}}
    if corruption == "hash": manifest["files"]["corpus.json"] = "0" * 64
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        load_package(tmp_path, tmp_path / "development.json")
