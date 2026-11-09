"""注意力候选仅改变转换路径，完整样本/分值/排序及原门槛保留。"""
from copy import deepcopy

import pytest

from scripts.compare_attention_pressure import compare_attention, native_runtime
from scripts.compare_gpu_batch import attention_command


def groups():
    result = []
    for variant, latency in [("original-A1",2900), ("native-B",1800), ("original-A2",3000)]:
        result.append({"variant":variant,"workload_sha256":"same", "groups":[
            {"concurrency":c,"rows":[{"case_id":str(i+offset), "status":"PASS",
              "rank":[str(n) for n in range(20)], "rerank_scores":{str(n):float(n) for n in range(20)}}
                for i in range(100)],
             "summary":{"observed":100,"passed":100,"rerank_p95_ms_all_attempts":latency}}
            for c,offset in [(1,0),(10,100)]]})
    return result


def test_attention_command_keeps_weights_precision_candidates_and_batch():
    command = ["v2","--model-id","BAAI/bge-reranker-v2-m3","--device","cuda","--dtype","float16",
        "--port","7997","--batch-size","4","--no-compile","--no-model-warmup"]
    assert attention_command(command) == [*command, "--no-bettertransformer"]
    changed = attention_command(command, 8)
    assert changed[changed.index("--batch-size")+1] == "8"
    assert changed[-1] == "--no-bettertransformer"
    with pytest.raises(RuntimeError): attention_command(command, 16)
    with pytest.raises(RuntimeError): attention_command(command+["--dtype","int8"])


@pytest.mark.parametrize("bad", ["empty", "eager", "dtype", "class"])
def test_unverified_loaded_attention_or_precision_blocks_pressure(bad):
    import json
    marker = {"attention_implementation":"sdpa", "dtype":"torch.float16",
        "attention_classes":["XLMRobertaSdpaSelfAttention"]}
    if bad == "empty": text = "ordinary health response"
    else:
        if bad == "eager": marker["attention_implementation"] = "eager"
        elif bad == "dtype": marker["dtype"] = "torch.float32"
        else: marker["attention_classes"] = ["OtherSelfAttention"]
        text = "AICSHUB_ATTENTION_RUNTIME="+json.dumps(marker)
    with pytest.raises(RuntimeError): native_runtime(text)


def test_loaded_native_sdpa_marker_is_saved_before_request_logs():
    import json
    marker = {"attention_implementation":"sdpa", "dtype":"torch.float16",
        "attention_classes":["XLMRobertaSdpaSelfAttention"]}
    assert native_runtime("AICSHUB_ATTENTION_RUNTIME="+json.dumps(marker)) == [marker]


def test_docker_reads_chinese_mount_paths_as_utf8(monkeypatch):
    from scripts import compare_gpu_batch as module
    seen = {}
    def output(*args, **kwargs):
        seen.update(kwargs)
        return "中文挂载路径\n"
    monkeypatch.setattr(module.subprocess, "check_output", output)
    assert module.docker("inspect", "synthetic") == "中文挂载路径"
    assert seen["encoding"] == "utf-8"


def test_score_delta_and_ranking_differences_are_retained_without_business_pass():
    sample = groups()
    sample[1]["groups"][1]["rows"][0]["rerank_scores"]["1"] += .01
    sample[1]["groups"][1]["rows"][0]["rank"].reverse()
    result = compare_attention(sample)
    assert result["pairs"][0]["full_rank_equal"] == 199
    assert result["pairs"][0]["top1_equal"] == 199
    assert result["pairs"][0]["max_rounded_score_abs_delta"] == pytest.approx(.01)
    assert result["whole_m5_pass"] is result["business_switch_approved"] is False


@pytest.mark.parametrize("bad", ["missing", "duplicate", "nan", "workload", "rank", "latency", "variant"])
def test_missing_scores_and_mixed_data_never_form_accepted_comparison(bad):
    sample = deepcopy(groups())
    row = sample[1]["groups"][1]["rows"][0]
    if bad == "missing": row["rerank_scores"].pop("1")
    elif bad == "duplicate": row["case_id"] = "0"
    elif bad == "nan": row["rerank_scores"]["1"] = float("nan")
    elif bad == "workload": sample[1]["workload_sha256"] = "different"
    elif bad == "rank": row["rank"] = ["0"]*20
    elif bad == "latency": sample[1]["groups"][1]["summary"]["rerank_p95_ms_all_attempts"] = float("inf")
    else: sample[1]["variant"] = "int8"
    with pytest.raises((RuntimeError, ValueError)): compare_attention(sample)
