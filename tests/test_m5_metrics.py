import json
from math import log2

import pytest
from pydantic import ValidationError
from scripts.evaluate_m5 import Chunk, RelevantChunk, Dataset, Run, ranking_metrics, summarize, validate_splits


def dataset():
    return Dataset.model_validate({"schema_version":1,"split":"development","label_source":"assistant_draft",
        "cases":[{"case_id":"q1","cluster_id":"policy1","query":"合成退货条件",
                  "kind":"answerable","relevant":[{"chunk_id":"a","version_id":"v1","grade":3},
                      {"chunk_id":"b","version_id":"v2","grade":1}],
                  "required_conditions":["定制品例外"],"expected_support":"supported"},
                 {"case_id":"q2","cluster_id":"negative1","query":"未收录型号是否支持潜水",
                  "kind":"unanswerable","expected_support":"unknown"}]})


def run(rows):
    return Run.model_validate({"schema_version":1,"dataset_sha256":"a"*64,"source_fingerprint":"b"*64,
        "layer":"T0","mode":"hybrid","cache":"cold","configuration":{"embedding":"synthetic"},
        "output_label_source":"unreviewed","rows":rows})


def observation(case_id="q1"):
    return {"case_id":case_id,"ranked_chunks":[{"chunk_id":"a","version_id":"v1"}],
        "evidence_chunks":[{"chunk_id":"a","version_id":"v1"}],"covered_conditions":["定制品例外"],
        "observed_support":"supported","accepted":True,"latency_ms":10.0}


def test_recall_requires_all_necessary_chunks_not_any_title_hit():
    value = ranking_metrics(dataset().cases[0].relevant, [Chunk(chunk_id="a",version_id="v1")])
    assert value["recall_at_20"] == .5 and value["evidence_hit_at_20"] == 1
    assert value["ndcg_at_5"] == pytest.approx(7/(7+1/log2(3)))


def test_same_chunk_wrong_version_does_not_count():
    value = ranking_metrics(dataset().cases[0].relevant, [Chunk(chunk_id="a",version_id="obsolete")])
    assert value["recall_at_20"] == value["mrr"] == value["ndcg_at_5"] == 0


def test_rank_twenty_counts_but_rank_twenty_one_does_not():
    relevant=[RelevantChunk(chunk_id="answer",version_id="v",grade=1)]
    distractors=[Chunk(chunk_id=f"x{i}",version_id="v") for i in range(20)]
    assert ranking_metrics(relevant,distractors[:19]+[Chunk(chunk_id="answer",version_id="v")])["recall_at_20"] == 1
    assert ranking_metrics(relevant,distractors+[Chunk(chunk_id="answer",version_id="v")])["recall_at_20"] == 0


def test_missing_second_required_evidence_cannot_pass_condition_coverage():
    result=summarize(dataset(), run([observation()]), "a"*64)
    assert result["metrics"]["condition_coverage"] == 0
    assert result["metrics"]["unanswerable_correct"] == 0
    assert result["metrics"]["claim_support"] is None
    assert result["status"] == "FAIL" and not result["whole_m5_pass"]


def test_empty_observations_never_default_pass_and_keep_missing_denominator():
    result=summarize(dataset(),run([]),"a"*64)
    assert result["status"]=="FAIL" and result["metrics"]["latency_p95_ms"] is None
    assert len(result["details"])==2 and result["metrics"]["recall_at_20"]==0


def test_assistant_claim_labels_cannot_establish_human_support_metric():
    row=observation(); row["claim_support"]=[True]
    result=summarize(dataset(),run([row]),"a"*64)
    assert result["metrics"]["claim_support"] is None
    assert "independent_holdout_review_missing" in result["reasons"]


@pytest.mark.parametrize("change", ["duplicate_result", "unknown_result", "hash"])
def test_invalid_observation_identity_rejected(change):
    rows=[observation()]
    if change=="duplicate_result": rows*=2
    if change=="unknown_result": rows=[observation("foreign")]
    with pytest.raises(ValueError):
        summarize(dataset(),run(rows),"c"*64 if change=="hash" else "a"*64)


@pytest.mark.parametrize("change", ["empty", "duplicate_case", "duplicate_query", "human_without_reviewer"])
def test_dataset_integrity(change):
    value=dataset().model_dump()
    if change=="empty": value["cases"]=[]
    if change=="duplicate_case": value["cases"].append(value["cases"][0])
    if change=="duplicate_query": value["cases"][1]["query"]=value["cases"][0]["query"]
    if change=="human_without_reviewer": value["label_source"]="human_reviewed"
    with pytest.raises(ValidationError): Dataset.model_validate(value)


def test_nan_latency_or_forged_evidence_is_rejected():
    row=observation(); row["latency_ms"]=float("nan")
    with pytest.raises(ValidationError): run([row])


def test_semantic_cluster_cannot_cross_development_and_holdout():
    dev=dataset()
    value=dev.model_dump(); value["split"]="holdout"
    for case in value["cases"]:
        case["case_id"] += "-holdout"
        case["query"] += "换个说法"
    with pytest.raises(ValueError,match="cluster leakage"):
        validate_splits(dev,Dataset.model_validate(value))
    row=observation(); row["evidence_chunks"][0]["version_id"]="foreign"
    with pytest.raises(ValidationError): run([row])


def test_cli_invalid_input_records_nonpass_without_echoing_sensitive_data(tmp_path,monkeypatch):
    import sys
    from scripts.evaluate_m5 import main
    data=tmp_path/"data.json"; rows=tmp_path/"run.json"; output=tmp_path/"report.json"
    data.write_text('{"invalid":"synthetic-secret"}',encoding="utf-8")
    rows.write_text('{}',encoding="utf-8")
    monkeypatch.setattr(sys,"argv",["evaluate_m5","--dataset",str(data),"--observations",str(rows),"--output",str(output)])
    assert main()==2
    report=json.loads(output.read_text(encoding="utf-8"))
    assert report["status"]=="INVALID_INPUT" and "synthetic-secret" not in output.read_text()
