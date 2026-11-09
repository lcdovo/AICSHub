"""核验助手复核的范围/证据/来源；不能把委托复核转换为独立人工金标。"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def case_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def validate_review(review: dict, dataset: dict, corpus: list[dict], human: dict,
                    hashes: dict[str, str]) -> dict:
    if (review.get("reviewer") != "assistant" or review.get("independent_human_review") is not False
            or review.get("whole_m5_pass") is not False or review.get("external_model_calls") != 0):
        raise ValueError("assistant_review_cannot_be_human_or_acceptance")
    for key, digest in hashes.items():
        if review.get(key) != digest:
            raise ValueError("review_material_hash_changed")
    original = {case["case_id"]: case for case in dataset["cases"]}
    humans = {case["case_id"]: case for case in human["cases"]}
    if (len(original) != len(dataset["cases"]) or len(humans) != len(human["cases"])
            or dataset.get("label_source") != "assistant_draft" or human.get("label_source") != "human_reviewed"
            or set(review.get("protected_human_case_ids", [])) != humans.keys()):
        raise ValueError("human_materials_not_preserved")
    for key, case in humans.items():
        if key not in original or case != original[key]:
            raise ValueError("explicit_human_label_modified")
    records = review.get("records", [])
    ids = [row["case_id"] for row in records]
    if len(ids) != len(set(ids)) or set(ids) != original.keys() - humans.keys():
        raise ValueError("review_does_not_cover_exact_remaining_cases")
    chunks = {(row["chunk_id"], row["version_id"]): row["content"] for row in corpus}
    for row in records:
        case = original[row["case_id"]]
        if (row.get("reviewer") != "assistant" or row.get("independent_human_review") is not False
                or row.get("case_sha256") != case_digest(case) or row.get("kind") != case["kind"]
                or row.get("disposition") not in {"consistent", "needs_revision"}
                or not row.get("reason") or not row.get("category")):
            raise ValueError("invalid_review_record")
        if row["disposition"] == "needs_revision" and not row.get("proposed_revision"):
            raise ValueError("revision_without_action")
        for anchor in row.get("evidence", []):
            body = chunks.get((anchor["chunk_id"], anchor["version_id"]))
            if not body or not anchor.get("quote") or anchor["quote"] not in body:
                raise ValueError("review_evidence_missing_changed_or_fabricated")
    counts = dict(Counter(row["disposition"] for row in records))
    if (counts != review.get("counts") or review.get("assistant_reviewed_count") != len(records)
            or review.get("human_reviewed_count") != len(humans)):
        raise ValueError("review_count_mismatch")
    return {"status": "PASS_REVIEW_ARTIFACT_INTEGRITY_ONLY", "human_reviewed_count": len(humans),
            "assistant_reviewed_count": len(records), "counts": counts,
            "whole_m5_pass": False, "independent_human_quality_gate_pass": False,
            "limit": "source hashes and evidence quotes consistent; assistant judgments are not independent human labels or real model results"}


def main() -> int:
    from scripts.test_provenance import snapshot_sources, compare_sources
    output = ROOT / "reports/m5" / datetime.now().strftime("assistant-review-audit-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    paths = {"dataset_sha256": ROOT / "docs/M5-HOLDOUT-DRAFT-v1.json",
             "corpus_sha256": ROOT / "docs/M5-HOLDOUT-CORPUS-DRAFT-v1.json",
             "human_partial_sha256": ROOT / "docs/M5-HOLDOUT-REVIEWED-PARTIAL-v1.json",
             "source_sha256": ROOT / "docs/M5-HOLDOUT-SOURCE-DRAFT-v1.json"}
    review_path = ROOT / "docs/M5-ASSISTANT-REVIEW-v1.json"
    before = snapshot_sources(ROOT)
    report = {"status": "INCOMPLETE", "whole_m5_pass": False}
    hashes = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items()}
    review_hash = hashlib.sha256(review_path.read_bytes()).hexdigest()
    try:
        report.update(validate_review(json.loads(review_path.read_bytes()),
            json.loads(paths["dataset_sha256"].read_bytes()), json.loads(paths["corpus_sha256"].read_bytes()),
            json.loads(paths["human_partial_sha256"].read_bytes()), hashes))
    except (ValueError, KeyError, TypeError):
        report["status"] = "FAIL_REVIEW_ARTIFACT"
    finally:
        after = snapshot_sources(ROOT)
        report.update(compare_sources(before, after))
        report["input_hashes"] = {**hashes, "review_sha256": review_hash}
        report["input_materials_stable"] = all(hashlib.sha256(path.read_bytes()).hexdigest() == hashes[key]
            for key, path in paths.items()) and hashlib.sha256(review_path.read_bytes()).hexdigest() == review_hash
        if not report["source_tree_stable"] or not report["source_snapshot_complete"] or not report["input_materials_stable"]:
            report["status"] = "FAIL_SOURCE_OR_MATERIAL_DRIFT"
        for name, value in [("report", report), ("source-start", before), ("source-end", after)]:
            (output / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output / "report.json")}))
    return 0 if report["status"] == "PASS_REVIEW_ARTIFACT_INTEGRITY_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
