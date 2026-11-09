"""只读已人工裁决样本的BM25基线；不调用模型，不提升为完整质量验收。"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import runpy
from statistics import mean
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def validate_review(draft: dict, reviewed: dict, corpus: list[dict], decisions: list[dict],
                    dataset_hash: str, corpus_hash: str) -> dict:
    from scripts.evaluate_m5 import Dataset
    original, partial = Dataset.model_validate(draft), Dataset.model_validate(reviewed)
    if (original.label_source != "assistant_draft" or partial.label_source != "human_reviewed"
            or partial.split != "holdout"):
        raise ValueError("invalid_review_roles")
    by_id = {case.case_id: case for case in original.cases}
    chunks = {(item["chunk_id"], item["version_id"]): item for item in corpus}
    if len(chunks) != len(corpus):
        raise ValueError("duplicate_corpus_chunk_version")
    approved = set()
    for record in decisions:
        if (record.get("reviewer") != partial.reviewer or not record.get("reviewed_at")
                or not record.get("user_statement") or record.get("dataset_sha256") != dataset_hash
                or record.get("corpus_sha256") != corpus_hash):
            raise ValueError("review_record_provenance_mismatch")
        for decision in record.get("decisions", []):
            key = decision.get("case_id")
            if (decision.get("decision") != "approved" or key not in by_id
                    or key in approved or decision.get("kind") != by_id[key].kind):
                raise ValueError("invalid_duplicate_or_unknown_decision")
            approved.add(key)
    if approved != {case.case_id for case in partial.cases}:
        raise ValueError("partial_dataset_not_exactly_approved")
    for case in partial.cases:
        if case.model_dump() != by_id[case.case_id].model_dump():
            raise ValueError("approved_label_changed")
        if any(item.key() not in chunks for item in case.relevant):
            raise ValueError("reviewed_evidence_missing_or_wrong_version")
    return {"approved_count": len(approved), "remaining_count": len(original.cases) - len(approved),
            "approved_counts_by_kind": dict(Counter(case.kind for case in partial.cases)),
            "reviewer": partial.reviewer, "whole_dataset_approved": False,
            "global_semantic_independence_verified": False,
            "limit": "records reflect explicit user decisions in this chat; hashes verify material consistency, not independent reviewer identity"}


def lexical_baseline(reviewed: dict, corpus: list[dict]) -> dict:
    from scripts.evaluate_m5 import Dataset, Chunk, ranking_metrics
    scorer_class = runpy.run_path(str(ROOT / "app/services/knowledge_base/bm25.py"))["BM25Scorer"]
    scorer = scorer_class()
    scorer.build_index([item["content"] for item in corpus])
    rows = []
    dataset = Dataset.model_validate(reviewed)
    for case in dataset.cases:
        if case.kind != "answerable":
            continue  # 词法排序不冒充拒答或Judge结果，其他类别另列未测。
        scored = [(index, scorer.score(case.query, index)) for index in range(len(corpus))]
        if any(not math.isfinite(score) for _, score in scored):
            raise ValueError("nonfinite_lexical_score")
        ranked = sorted(((i, value) for i, value in scored if value > 0), key=lambda pair: -pair[1])[:20]
        chunks = [Chunk(chunk_id=corpus[i]["chunk_id"], version_id=corpus[i]["version_id"]) for i, _ in ranked]
        rows.append({"case_id": case.case_id, "ranked_chunks": [chunk.model_dump() for chunk in chunks],
                     "bm25_scores": [value for _, value in ranked],
                     **ranking_metrics(case.relevant, chunks)})
    return {"mode": "bm25", "layer": "T0_LEXICAL_ONLY", "corpus_chunks": len(corpus),
            "evaluated_answerable_cases": len(rows), "rows": rows,
            "metrics": {name: mean(row[name] for row in rows) if rows else None
                        for name in ["recall_at_20", "mrr", "ndcg_at_5", "evidence_hit_at_20"]},
            "generated_answers": 0, "model_calls": 0, "whole_m5_pass": False,
            "not_measured": ["unanswerable_correct", "claim_support", "condition_coverage",
                             "judge_false_accept", "judge_false_reject", "dense", "hybrid", "rerank", "rewrite",
                             "full_business_chain", "performance"]}


def main() -> int:
    from scripts.test_provenance import snapshot_sources, compare_sources
    from scripts.evaluate_m5 import Dataset, validate_splits
    output = ROOT / "reports/m5" / datetime.now().strftime("reviewed-bm25-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    before = snapshot_sources(ROOT)
    report = {"status": "INCOMPLETE", "business_changed": False, "external_model_calls": 0,
              "whole_m5_pass": False, "performance_gate": "DEFERRED_BY_USER_NOT_PASSED"}
    paths = [ROOT / "docs" / name for name in ["M5-HOLDOUT-DRAFT-v1.json", "M5-HOLDOUT-CORPUS-DRAFT-v1.json",
             "M5-HOLDOUT-REVIEWED-PARTIAL-v1.json", "M5-GATES-v1.json"]]
    inputs = {}
    try:
        original, corpus, reviewed, gates = [json.loads(path.read_bytes()) for path in paths]
        decisions = []
        for relative in gates["human_review"]["decision_records"]:
            path = (ROOT / relative).resolve()
            if not path.is_relative_to((ROOT / "docs/m5-review").resolve()):
                raise ValueError("review_record_outside_review_directory")
            paths.append(path)
            decisions.append(json.loads(path.read_bytes()))
        paths.append(ROOT / "acceptance/m5/development.json")
        inputs = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        report["review_audit"] = validate_review(original, reviewed, corpus, decisions,
            inputs["docs/M5-HOLDOUT-DRAFT-v1.json"], inputs["docs/M5-HOLDOUT-CORPUS-DRAFT-v1.json"])
        validate_splits(Dataset.model_validate_json(paths[-1].read_bytes()), Dataset.model_validate(reviewed))
        if set(gates["human_review"]["approved_case_ids"]) != {case["case_id"] for case in reviewed["cases"]}:
            raise ValueError("gate_review_count_mismatch")
        report["baseline"] = lexical_baseline(reviewed, corpus)
        report["status"] = "PARTIAL_REVIEWED_LEXICAL_BASELINE_NOT_ACCEPTANCE"
    except Exception as exc:
        report.update(status="INVALID_INPUT_OR_BASELINE_FAILED", error_type=type(exc).__name__)
        if isinstance(exc, ValueError):
            report["error"] = "review_or_baseline_validation_failed"
    finally:
        after = snapshot_sources(ROOT)
        report.update(compare_sources(before, after))
        report["input_hashes"] = inputs
        report["input_materials_stable"] = bool(inputs) and all(
            path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == inputs.get(path.relative_to(ROOT).as_posix())
            for path in paths)
        if not report["source_tree_stable"] or not report["source_snapshot_complete"] or not report["input_materials_stable"]:
            report["status"] = "FAIL_SOURCE_OR_REVIEW_MATERIAL_DRIFT"
        for name, value in [("report", report), ("source-start", before), ("source-end", after)]:
            (output / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output / "report.json")}))
    return 0 if report["status"] == "PARTIAL_REVIEWED_LEXICAL_BASELINE_NOT_ACCEPTANCE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
