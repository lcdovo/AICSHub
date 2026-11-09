"""图融合仅在已核实架构与独立产物中进行，融合数量不充当质量/容量验收。"""
import pytest

from scripts.onnx_candidate.optimize_model import validate_architecture, validate_fusions
from scripts.verify_onnx_candidate import container_command


def config():
    return {"model_type": "xlm-roberta", "architectures": ["XLMRobertaForSequenceClassification"],
            "num_hidden_layers": 24, "num_attention_heads": 16, "hidden_size": 1024}


@pytest.mark.parametrize("field,value", [("model_type", "bert"), ("architectures", ["Other"]),
                                         ("num_hidden_layers", 12), ("num_attention_heads", 8),
                                         ("hidden_size", 768)])
def test_other_architecture_does_not_silently_use_fixed_fusion_shape(field, value):
    data = {**config(), field: value}
    with pytest.raises(ValueError):
        validate_architecture(data)


@pytest.mark.parametrize("count", [0, 1, 23, 25])
def test_partial_or_excess_fusion_never_claims_complete_model(count):
    result = validate_fusions({"Attention": count})
    assert not result["attention_fusion_complete"]
    assert not result["whole_m5_pass"] and not result["business_switch_approved"]


@pytest.mark.parametrize("count", [-1, True, "24"])
def test_invalid_counts_rejected(count):
    with pytest.raises(ValueError):
        validate_fusions({"Attention": count})


def test_actual_complete_fusion_only_enters_validation():
    result = validate_fusions({"Attention": 0, "MultiHeadAttention": 24})
    assert result["status"] == "FUSED_NOT_VALIDATED"
    assert not result["whole_m5_pass"]
    assert validate_architecture(config()) == (16, 1024)


def test_source_export_and_original_weights_remain_readonly_without_network(tmp_path):
    command = container_command("optimize", "sha256:" + "a" * 64, tmp_path / "new-output", "owned", tmp_path / "old-export")
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command and "--publish" not in command
    mounts = [command[i + 1] for i, value in enumerate(command) if value == "--mount"]
    assert len(mounts) == 4
    assert all(value.endswith(",readonly") for value in mounts if "target=/onnx-output" not in value)
    assert str(tmp_path / "new-output") in next(value for value in mounts if "target=/onnx-output" in value)


def test_fusion_requires_verified_input_export(tmp_path):
    with pytest.raises(ValueError, match="export_directory_required"):
        container_command("optimize", "sha256:" + "a" * 64, tmp_path, "owned")


def groups():
    import copy
    rows = [{"case_id": f"case-{i}", "status": "COMPLETE", "logits": [float(j) for j in range(20)],
             "rank": list(reversed(range(20))), "rerank_ms": 2500.} for i in range(200)]
    data = {"status": "DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE",
            "comparisons": [{"numeric_tolerance_pass": True}] * 12,
            "groups": [{"concurrency": 1, "rows": rows[:100]}, {"concurrency": 10, "rows": rows[100:]}]}
    stage = {"workload_sha256": "f" * 64, "status": "PASS_STAGE_ONLY", "source_tree_stable": True,
             "source_snapshot_complete": True, "original_unchanged": True, "final_state": {"Running": False},
             "start_source_fingerprint": "a" * 64}
    return [{"variant": variant, "comparison": copy.deepcopy(data), "stage_report": copy.deepcopy(stage)}
            for variant in ["original-A1", "fusion-B", "original-A2"]]


def test_partial_optimizations_do_not_change_original_latency_gate():
    from scripts.compare_onnx_fusion import validate_comparison
    result = validate_comparison(groups())
    assert result["concurrent10_direct_model_p95_ms"] == [2500.] * 3
    assert not result["candidate_within_original_direct_latency_gate"]
    assert not result["candidate_adopted"] and not result["whole_m5_pass"]


@pytest.mark.parametrize("bad", ["order", "workload", "source", "busy", "missing", "duplicate", "nan", "scores", "numeric", "running"])
def test_invalid_aba_evidence_is_not_reported_as_complete(bad):
    from scripts.compare_onnx_fusion import validate_comparison
    data = groups()
    if bad == "order": data.reverse()
    elif bad == "workload": data[1]["stage_report"]["workload_sha256"] = "other"
    elif bad == "source": data[1]["stage_report"]["start_source_fingerprint"] = "other"
    elif bad == "busy": data[1]["comparison"]["groups"][1]["rows"][0]["status"] = "BUSY"
    elif bad == "missing": data[1]["comparison"]["groups"][1]["rows"].pop()
    elif bad == "duplicate": data[1]["comparison"]["groups"][1]["rows"][0]["case_id"] = "case-0"
    elif bad == "nan": data[1]["comparison"]["groups"][1]["rows"][0]["rerank_ms"] = float("nan")
    elif bad == "scores": data[1]["comparison"]["groups"][1]["rows"][0]["logits"].pop()
    elif bad == "numeric": data[1]["comparison"]["comparisons"] = []
    else: data[1]["stage_report"]["final_state"]["Running"] = True
    with pytest.raises(ValueError):
        validate_comparison(data)
