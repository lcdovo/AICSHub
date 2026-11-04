"""M5 chunk/version级离线报告器。不调用模型，不把缺项或助手自标数据算完整验收。"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Chunk(StrictModel):
    chunk_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)

    def key(self):
        return self.chunk_id, self.version_id


class RelevantChunk(Chunk):
    grade: int = Field(ge=1, le=3)


class Case(StrictModel):
    case_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    kind: Literal["answerable", "unanswerable", "review_negative", "review_positive"]
    relevant: list[RelevantChunk] = Field(default_factory=list)
    required_conditions: list[str] = Field(default_factory=list)
    expected_support: Literal["supported", "partial", "unsupported", "unknown"]
    p0: bool = False
    candidate: str | None = None
    tool_results: list[dict] = Field(default_factory=list)
    review_reason: str | None = None

    @model_validator(mode="after")
    def labels(self):
        if self.kind == "answerable" and not self.relevant:
            raise ValueError("answerable requires chunk/version labels")
        if len({c.key() for c in self.relevant}) != len(self.relevant):
            raise ValueError("duplicate relevant chunk")
        if self.kind == "unanswerable" and self.expected_support == "supported":
            raise ValueError("unanswerable cannot expect full support")
        if len(set(self.required_conditions)) != len(self.required_conditions):
            raise ValueError("duplicate condition labels")
        return self


class Dataset(StrictModel):
    schema_version: Literal[1]
    split: Literal["development", "holdout"]
    label_source: Literal["assistant_draft", "human_reviewed"]
    reviewer: str | None = None
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({c.case_id for c in self.cases}) != len(self.cases):
            raise ValueError("duplicate case_id")
        normalized = ["".join(c.query.split()).casefold() for c in self.cases]
        if len(set(normalized)) != len(normalized):
            raise ValueError("duplicate query")
        if self.label_source == "human_reviewed" and not (self.reviewer and self.reviewer.strip()):
            raise ValueError("human reviewer required")
        return self


class Observation(StrictModel):
    case_id: str
    ranked_chunks: list[Chunk] = Field(default_factory=list)
    evidence_chunks: list[Chunk] = Field(default_factory=list)
    covered_conditions: list[str] = Field(default_factory=list)
    observed_support: Literal["supported", "partial", "unsupported", "unknown"]
    accepted: bool
    latency_ms: float = Field(ge=0)
    error: str | None = None
    # 独立输出审阅者标注，不读Judge自评分；None表示未标注。
    claim_support: list[bool] | None = None

    @model_validator(mode="after")
    def unique_chunks(self):
        if len({c.key() for c in self.ranked_chunks}) != len(self.ranked_chunks):
            raise ValueError("duplicate ranked chunk")
        if len({c.key() for c in self.evidence_chunks}) != len(self.evidence_chunks):
            raise ValueError("duplicate evidence chunk")
        if not {c.key() for c in self.evidence_chunks} <= {c.key() for c in self.ranked_chunks}:
            raise ValueError("generation evidence must be present in ranked results")
        return self


class Run(StrictModel):
    schema_version: Literal[1]
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    layer: Literal["T0", "T1_LOCAL_MODELS", "T2"]
    mode: Literal["bm25", "dense", "hybrid", "rerank", "rewrite"]
    cache: Literal["cold", "hot"]
    configuration: dict
    output_label_source: Literal["unreviewed", "assistant_draft", "human_reviewed"]
    output_reviewer: str | None = None
    rows: list[Observation]


def validate_splits(development: Dataset, holdout: Dataset) -> None:
    if development.split != "development" or holdout.split != "holdout":
        raise ValueError("wrong dataset split roles")
    for extractor in (lambda c: c.case_id, lambda c: c.cluster_id, lambda c: "".join(c.query.split()).casefold()):
        if {extractor(c) for c in development.cases} & {extractor(c) for c in holdout.cases}:
            raise ValueError("development/holdout identity or semantic cluster leakage")


def ranking_metrics(relevant: list[RelevantChunk], ranked: list[Chunk]) -> dict:
    if not relevant:
        raise ValueError("retrieval metrics require nonempty relevance labels")
    grades = {c.key(): c.grade for c in relevant}
    keys = [c.key() for c in ranked]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate ranked chunks")
    recall = len(set(keys[:20]) & grades.keys()) / len(grades)
    hit = float(any(k in grades for k in keys[:20]))
    mrr = next((1 / rank for rank, key in enumerate(keys, 1) if key in grades), 0.0)
    dcg = sum((2 ** grades.get(key, 0) - 1) / math.log2(rank + 1) for rank, key in enumerate(keys[:5], 1))
    ideal = sum((2 ** grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(sorted(grades.values(), reverse=True)[:5], 1))
    return {"recall_at_20": recall, "evidence_hit_at_20": hit, "mrr": mrr, "ndcg_at_5": dcg / ideal}


def summarize(dataset: Dataset, run: Run, dataset_hash: str) -> dict:
    if run.dataset_sha256 != dataset_hash:
        raise ValueError("dataset hash mismatch")
    by_id = {r.case_id: r for r in run.rows}
    if len(by_id) != len(run.rows) or set(by_id) - {c.case_id for c in dataset.cases}:
        raise ValueError("duplicate/unknown result case_id")
    details = []
    for case in dataset.cases:
        row = by_id.get(case.case_id)
        failed = row is None or row.error is not None
        entry = {"case_id": case.case_id, "kind": case.kind, "error": "missing_result" if row is None else row.error}
        if case.kind == "answerable":
            entry.update(ranking_metrics(case.relevant, row.ranked_chunks if row and not failed else []))
            evidence = {c.key() for c in row.evidence_chunks} if row and not failed else set()
            entry["condition_coverage"] = float(not failed and {c.key() for c in case.relevant} <= evidence
                and set(case.required_conditions) <= set(row.covered_conditions))
        elif case.kind == "unanswerable":
            entry["unanswerable_correct"] = float(not failed and not row.accepted
                and row.observed_support == case.expected_support)
        elif case.kind == "review_negative":
            # 执行失败不能算正确阻断；缺结果与错误均记为未满足安全门禁。
            entry["false_accept"] = float(failed or row.accepted)
            entry["p0"] = case.p0
        else:
            entry["false_reject"] = float(failed or not row.accepted)
        details.append(entry)
    names = ["recall_at_20", "evidence_hit_at_20", "mrr", "ndcg_at_5", "condition_coverage", "unanswerable_correct", "false_accept", "false_reject"]
    metrics = {name: mean([r[name] for r in details if name in r]) if any(name in r for r in details) else None for name in names}
    human_outputs = (run.output_label_source == "human_reviewed" and bool(run.output_reviewer and run.output_reviewer.strip()))
    claim_rows = [by_id.get(c.case_id) for c in dataset.cases if c.kind == "answerable"]
    claim_labels = [b for row in claim_rows if row and not row.error for b in (row.claim_support or [])]
    claims_complete = bool(claim_rows) and all(row and not row.error and row.claim_support for row in claim_rows)
    metrics["claim_support"] = mean(claim_labels) if human_outputs and claims_complete else None
    metrics["p0_false_accept_count"] = sum(int(r["false_accept"]) for r in details if r.get("p0"))
    latencies = sorted(r.latency_ms for r in run.rows if r.error is None)
    metrics["latency_p95_ms"] = latencies[math.ceil(.95 * len(latencies)) - 1] if latencies else None
    reasons = []
    counts = {kind: sum(c.kind == kind for c in dataset.cases) for kind in ["answerable", "unanswerable", "review_negative", "review_positive"]}
    for kind, minimum in {"answerable":300,"unanswerable":60,"review_negative":100,"review_positive":100}.items():
        if counts[kind] < minimum:
            reasons.append("insufficient_" + kind)
    if dataset.split != "holdout" or dataset.label_source != "human_reviewed":
        reasons.append("independent_holdout_review_missing")
    if not human_outputs or not claims_complete:
        reasons.append("independent_output_claim_review_missing")
    if run.layer == "T0":
        reasons.append("real_model_layer_missing")
    required_config = {"embedding_model", "embedding_revision", "reranker_model", "prompt_version", "index_version"}
    if any(not isinstance(run.configuration.get(k), str) or not run.configuration[k].strip() for k in required_config):
        reasons.append("configuration_versions_missing")
    if any(r["error"] is not None for r in details):
        reasons.append("missing_or_failed_observations")
    if not any(r.get("p0") for r in details):
        reasons.append("p0_counterexamples_missing")
    thresholds = {"recall_at_20":(.95,True), "ndcg_at_5":(.85,True), "condition_coverage":(.95,True),
        "unanswerable_correct":(.95,True), "claim_support":(.95,True), "false_accept":(.02,False), "false_reject":(.05,False)}
    failures=[]
    for name, (limit, minimum) in thresholds.items():
        val=metrics[name]
        if val is None:
            reasons.append("unmeasured_" + name)
        elif (val < limit if minimum else val > limit):
            failures.append(name)
    if metrics["p0_false_accept_count"]:
        failures.append("p0_false_accept_count")
    return {"status":"FAIL" if failures else "INCOMPLETE" if reasons else "PASS_SINGLE_RUN_ONLY",
            "whole_m5_pass":False, "reasons":reasons, "failed_metrics":failures, "counts":counts,
            "metrics":metrics, "details":details, "mode":run.mode, "cache":run.cache, "layer":run.layer,
            "dataset_sha256":dataset_hash, "source_fingerprint":run.source_fingerprint,
            "configuration":run.configuration,
            "limits":["single run cannot prove all five ablations/cold-hot or M4 prerequisite", "reviewer metadata is asserted provenance, not independent identity verification", "provider invoice and model bill not established by this scorer"]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--development-dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args=parser.parse_args()
    raw=args.dataset.read_bytes()
    try:
        dataset=Dataset.model_validate(json.loads(raw))
        if args.development_dataset:
            development=Dataset.model_validate(json.loads(args.development_dataset.read_bytes()))
            validate_splits(development, dataset)
        run=Run.model_validate(json.loads(args.observations.read_bytes()))
        report=summarize(dataset, run, hashlib.sha256(raw).hexdigest())
        if dataset.split == "holdout" and args.development_dataset is None:
            report["reasons"].append("development_split_not_checked")
            if report["status"] == "PASS_SINGLE_RUN_ONLY":
                report["status"] = "INCOMPLETE"
    except (ValueError, TypeError) as exc:
        report={"status":"INVALID_INPUT", "whole_m5_pass":False, "reason":type(exc).__name__}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(report["status"])
    return 0 if report["status"] == "PASS_SINGLE_RUN_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
