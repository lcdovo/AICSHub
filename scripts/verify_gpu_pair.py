"""双GPU候选切换前验收：固定合成压力与新Milvus索引，不切业务路由、不访问付费供应商。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager, nullcontext
import concurrent.futures
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def percentile(values: list[float], fraction: float = .95):
    if not values:
        return None
    return sorted(values)[math.ceil(len(values) * fraction) - 1]


def workload(count: int) -> list[dict]:
    """200 distinct requests, 12 repeating semantic clusters: load fixtures, not quality holdout."""
    corpus = json.loads((ROOT / "acceptance/m5/corpus.json").read_bytes())
    rows = []
    for i in range(count):
        selected = corpus[i % len(corpus)]
        docs = []
        for j in range(20):
            original = corpus[(i + j) % len(corpus)]
            # Bounded long text includes the exception at the tail; no truncation in harness.
            content = (original["content"] + "合成评测说明：仅依此条款核验适用对象、数量、期限与例外。") * 4
            docs.append(f"合成资料{i:03d}-{j:02d}。{content}必要条件：以本条完整原文为准，不推定已执行任何申请。")
        rows.append({"case_id": f"load-{i:03d}", "cluster": selected["document_id"],
                     "query": f"合成独立请求{i:03d}：请核实{selected['title']}的适用范围与必要条件。",
                     "documents": docs})
    return rows


def validate_scores(items, expected: int):
    if not isinstance(items, list) or len(items) != expected:
        raise ValueError("incomplete_rerank_scores")
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("invalid_rerank_score")
        index, score = item.get("index"), item.get("relevance_score", item.get("score"))
        if (type(index) is not int or not 0 <= index < expected or index in seen
                or type(score) not in (int, float) or not math.isfinite(score)):
            raise ValueError("invalid_rerank_score")
        seen.add(index)


def summarize(rows: list[dict], expected: int) -> dict:
    good = [r for r in rows if r.get("status") == "PASS"]
    complete = expected > 0 and len(rows) == expected and len({r["case_id"] for r in rows}) == expected
    all_phase = [r["rerank_ms"] for r in rows if "rerank_ms" in r]
    metrics = {
        "expected": expected, "observed": len(rows), "passed": len(good),
        "failed": len(rows) - len(good), "missing": max(0, expected - len(rows)),
        "rerank_p95_ms_all_attempts": percentile(all_phase),
        "total_p95_ms_all_attempts": percentile([r["total_ms"] for r in rows]),
        "total_max_ms": max((r["total_ms"] for r in rows), default=None),
    }
    metrics["status"] = ("PASS_FINITE_LOAD_ONLY" if complete and len(good) == expected
                         and len(all_phase) == expected and percentile(all_phase) <= 2000 else "FAIL")
    return metrics


def inspect_candidate(name: str, port: int, internal: str) -> dict:
    obj = json.loads(subprocess.check_output(["docker", "inspect", name], encoding="utf-8"))[0]
    bindings = obj["NetworkSettings"]["Ports"].get(internal) or []
    if (obj["Config"]["Labels"].get("aicshub.test.owner") != "gpu-model-switch"
            or obj["State"]["Status"] != "running" or not obj["HostConfig"]["DeviceRequests"]
            or not any(b["HostIp"] == "127.0.0.1" and b["HostPort"] == str(port) for b in bindings)):
        raise RuntimeError("unverified_gpu_candidate")
    return {"name": name, "id": obj["Id"], "image_id": obj["Image"],
            "command": obj["Config"]["Cmd"], "gpu": obj["HostConfig"]["DeviceRequests"],
            "port": port}


def encode(client, port: int, texts: list[str]) -> list[list[float]]:
    raw = client.post(f"http://127.0.0.1:{port}/v1/embeddings",
                      json={"model": "qwen3-embedding:0.6b", "input": texts}).raise_for_status().json()
    items = raw.get("data")
    if not isinstance(items, list) or len(items) != len(texts):
        raise ValueError("embedding_count")
    vectors = [None] * len(texts)
    for item in items:
        i, v = item.get("index"), item.get("embedding")
        if (type(i) is not int or not 0 <= i < len(texts) or vectors[i] is not None
                or not isinstance(v, list) or len(v) != 1024
                or any(type(x) not in (int, float) or not math.isfinite(x) for x in v)):
            raise ValueError("invalid_embedding")
        vectors[i] = v
    return vectors


def compatibility(output: Path) -> dict:
    """CPU index + GPU query vs GPU index + GPU query; never writes an existing collection."""
    import httpx
    from scripts.evaluate_m5_local import verify_index_resource
    from app.services.knowledge_base.vector_index import VersionedVectorIndex
    port, _ = verify_index_resource(ROOT)
    corpus = json.loads((ROOT / "acceptance/m5/corpus.json").read_bytes())
    texts = [c["content"] for c in corpus]
    queries = [f"{c['title']}有哪些限制和例外？" for c in corpus]
    data = {}
    with httpx.Client(timeout=30, trust_env=False) as client:
        for device, endpoint in [("cpu", 8012), ("gpu", 8013)]:
            vectors = []
            for offset in range(0, len(texts + queries), 4):
                vectors.extend(encode(client, endpoint, (texts + queries)[offset:offset + 4]))
            data[device] = vectors
    index = VersionedVectorIndex("127.0.0.1", port, prefix="gpu_pair_compat_" + uuid.uuid4().hex[:12])
    fingerprints = {device: hashlib.sha256(("synthetic-device:" + device).encode()).hexdigest()
                    for device in data}
    chunks = [{"id": f"synthetic-{i}", "content": text} for i, text in enumerate(texts)]
    comparisons = []
    try:
        for device in data:
            index.write(uuid.uuid4().hex, chunks, data[device][:len(texts)], fingerprints[device], 1024)
        for i, query in enumerate(queries):
            ranks = {}
            for name, stored, query_device in [("cpu_cpu", "cpu", "cpu"),
                                                ("cpu_gpu", "cpu", "gpu"), ("gpu_gpu", "gpu", "gpu")]:
                hits = index.search(data[query_device][len(texts) + i], fingerprints[stored],
                                    [c["id"] for c in chunks], len(chunks))
                ranks[name] = [h["id"] for h in hits]
            comparisons.append({"case_id": f"compat-{i}", "ranks": ranks,
                                "mixed_top1_matches_cpu": ranks["cpu_cpu"][:1] == ranks["cpu_gpu"][:1],
                                "mixed_top3_overlap": len(set(ranks["cpu_cpu"][:3]) & set(ranks["cpu_gpu"][:3])) / 3})
        delta = []
        for left, right in zip(data["cpu"], data["gpu"]):
            norm = math.sqrt(sum(v*v for v in left) * sum(v*v for v in right))
            delta.append(sum(a*b for a, b in zip(left, right)) / norm)
        result = {"status": "OBSERVED_ONLY_NOT_MIXING_APPROVAL", "documents": len(texts),
                  "queries": len(queries), "min_vector_cosine": min(delta), "comparisons": comparisons,
                  "collections": [index.collection(f) for f in fingerprints.values()],
                  "top1_unchanged": sum(c["mixed_top1_matches_cpu"] for c in comparisons),
                  "limit": "small assistant development corpus; fingerprint spaces remain isolated; no production index mixing approved"}
        (output / "compatibility.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result
    finally:
        index.client.close()


@contextmanager
def scheduled_services(reranker_port=7999):
    """一个应用生命周期内复用服务；由创建者统一关闭，不由单条请求关闭。"""
    from app.services.embedding_service import APIEmbedding
    from app.services.reranker_service import RerankerService
    embedding = APIEmbedding("http://127.0.0.1:8013/v1", "local-only", "qwen3-embedding:0.6b",
                             dim=1024, max_retries=0, timeout=5, batch_size=4)
    try:
        reranker = RerankerService(local_base=f"http://127.0.0.1:{reranker_port}", local_model="BAAI/bge-reranker-v2-m3")
        try:
            yield embedding, reranker
        finally:
            reranker.close()
    finally:
        embedding.close()


def run_case_scheduled(case: dict, whole_request=False, services=None) -> dict:
    """真正使用应用两个模型服务的同进程HTTP出口；繁忙不算成功。"""
    from app.services.local_inference import inference_scope, LocalModelBusy, LocalModelDeadline
    from app.services.local_admission import request_scope
    events = []
    began = time.perf_counter()
    row = {"case_id": case["case_id"], "cluster": case["cluster"], "candidate_count": 20,
           "candidate_lengths": [len(t) for t in case["documents"]], "local_inference": events}
    try:
        with (scheduled_services() if services is None else nullcontext(services)) as (embedding, reranker), inference_scope(events=events):
            with (request_scope(purpose="retrieval") if whole_request else nullcontext()):
                start = time.perf_counter()
                embedding.encode_query(case["query"])
                row["embedding_ms"] = (time.perf_counter() - start) * 1000
                start = time.perf_counter()
                try:
                    result = reranker.rerank_with_deadline(
                        case["query"], [{"id": str(i), "content": text} for i, text in enumerate(case["documents"])],
                        20, deadline=time.monotonic() + 5)
                    if not result or len(result) != 20 or len({r["id"] for r in result}) != 20:
                        raise RuntimeError("rerank_failed_or_incomplete")
                    row["status"], row["rank"] = "PASS", [r["id"] for r in result]
                    row["rerank_scores"] = {r["id"]:r["rerank_score"] for r in result if "rerank_score" in r}
                finally:
                    row["rerank_ms"] = (time.perf_counter() - start) * 1000
    except Exception as exc:
        row["status"] = "BUSY" if isinstance(exc, LocalModelBusy) else "FAIL"
        row["error_type"] = type(exc).__name__
        if isinstance(exc, (LocalModelBusy, LocalModelDeadline)):
            row["error_code"] = str(exc)
    row["total_ms"] = (time.perf_counter() - began) * 1000
    return row


def run_case(case: dict, scheduled=False, whole_request=False, services=None, http_client=None) -> dict:
    if scheduled:
        return run_case_scheduled(case,whole_request,services)
    import httpx
    started = time.perf_counter()
    row = {"case_id": case["case_id"], "cluster": case["cluster"], "candidate_count": 20,
           "candidate_lengths": [len(t) for t in case["documents"]]}
    with (httpx.Client(timeout=5, trust_env=False) if http_client is None else nullcontext(http_client)) as client:
        try:
            began = time.perf_counter()
            encode(client, 8013, [case["query"]])
            row["embedding_ms"] = (time.perf_counter() - began) * 1000
            if row["embedding_ms"] > 5000:
                raise TimeoutError("late_embedding_result")
            began = time.perf_counter()
            try:
                raw = client.post("http://127.0.0.1:7999/rerank", json={
                    "model": "BAAI/bge-reranker-v2-m3", "query": case["query"],
                    "documents": case["documents"], "top_n": 20}).raise_for_status().json()
                validate_scores(raw.get("results"), 20)
                row["rank"] = [i["index"] for i in raw["results"]]
                row["status"] = "PASS"
            finally:
                row["rerank_ms"] = (time.perf_counter() - began) * 1000
            if row["rerank_ms"] > 5000:
                row["status"], row["error_type"] = "FAIL", "LateRerankResult"
        except Exception as exc:
            row["status"], row["error_type"] = "FAIL", type(exc).__name__
    row["total_ms"] = (time.perf_counter() - started) * 1000
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, choices=range(2, 201), default=200)
    parser.add_argument("--skip-compatibility", action="store_true")
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--shared-services", action="store_true")
    parser.add_argument("--whole-request",action="store_true")
    parser.add_argument("--coordinator",choices=["process","redis","batch"],default="process")
    parser.add_argument("--reranker-container", default="aicshub-gpu-v2m3-candidate")
    parser.add_argument("--reranker-port", type=int, default=7999)
    args = parser.parse_args()
    if args.whole_request and not args.scheduled:
        parser.error("--whole-request requires --scheduled")
    if args.shared_services and not args.scheduled:
        parser.error("--shared-services requires --scheduled")
    if args.coordinator=="redis" and not args.scheduled:
        parser.error("--coordinator redis requires --scheduled")
    if args.coordinator=="batch" and not (args.scheduled and args.shared_services and args.whole_request):
        parser.error("--coordinator batch requires --scheduled --shared-services --whole-request")
    if (args.reranker_container != "aicshub-gpu-v2m3-candidate" or args.reranker_port != 7999) and not (
            args.scheduled and args.shared_services and args.skip_compatibility):
        parser.error("temporary endpoint requires --scheduled --shared-services --skip-compatibility")
    from scripts.test_provenance import snapshot_sources, compare_sources
    output = ROOT / "reports/gpu-pair" / datetime.now().strftime("acceptance-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    before = snapshot_sources(ROOT)
    (output / "source-start.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    report = {"status": "INCOMPLETE", "whole_m5_pass": False, "business_changed": False,
              "external_calls": 0, "chat_calls": 0, "output": str(output),
              "layer": "real local provider load + isolated Milvus compatibility; not full app acceptance"}
    report["scheduled"] = args.scheduled
    report["whole_request"] = args.whole_request
    report["coordinator"] = args.coordinator
    report["service_lifecycle"] = "shared" if args.shared_services else "per_case"
    if args.scheduled:
        from app.config.config import settings
        settings.LOCAL_MODEL_COORDINATOR=args.coordinator
        if args.coordinator=="batch":
            settings.WORKERS=1
        if args.coordinator=="redis":
            from scripts.test_m1_real import verified_runtime
            _,runtime,owner=verified_runtime(ROOT,reuse_m3=True)
            settings.REDIS_ENABLED=True
            settings.REDIS_HOST="127.0.0.1"
            settings.REDIS_PORT=int(runtime["ports"]["redis"])
            settings.REDIS_PASSWORD=""
            settings.REDIS_DB=0
            settings.LOCAL_MODEL_COORDINATOR_NAMESPACE="aicshub:test:pressure:{"+uuid.uuid4().hex+"}"
            report["redis_test_owner"]=owner
            report["redis_namespace"]=settings.LOCAL_MODEL_COORDINATOR_NAMESPACE

    if args.scheduled:
        from app.services.local_admission import VERSION
        report["scheduler"] = {"version": VERSION, "scope": args.coordinator, "whole_request":args.whole_request,
                               "active": 10 if args.coordinator=="batch" else 1,
                               "waiters": 2, "queue_ms": 500, "background_wait_ms": 50}
        if args.coordinator=="batch":
            report["scheduler"].update({"embedding_active":1,"embedding_batch_items":20,
                "embedding_batch_window_ms":3,"rerank_active":2,"cross_worker":False})
    stop = threading.Event()
    memory = []

    def sample():
        while not stop.is_set():
            result = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.free,utilization.gpu",
                                     "--format=csv,noheader,nounits"], capture_output=True, text=True)
            if result.returncode == 0:
                memory.append({"time": time.time(), "raw": result.stdout.strip()})
            stop.wait(.5)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    resources = ExitStack()
    try:
        from scripts.evaluate_gpu_arrivals import verify_reranker
        report["runtime"] = [inspect_candidate("aicshub-gpu-embedding-candidate", 8013, "11434/tcp"),
                             verify_reranker(args.reranker_container, args.reranker_port)]
        import httpx
        with httpx.Client(timeout=5, trust_env=False) as client:
            tags = client.get("http://127.0.0.1:8013/api/tags").raise_for_status().json()
            report["embedding_digest"] = next(m["digest"] for m in tags["models"] if m["name"] == "qwen3-embedding:0.6b")
            models = client.get(f"http://127.0.0.1:{args.reranker_port}/models").raise_for_status().json()
            if not any(m["id"] == "BAAI/bge-reranker-v2-m3" for m in models["data"]):
                raise RuntimeError("wrong_reranker_model")
        if not args.skip_compatibility:
            report["compatibility"] = compatibility(output)
        services = resources.enter_context(scheduled_services(args.reranker_port)) if args.shared_services else None
        cases = workload(args.count)
        serialized = json.dumps(cases, ensure_ascii=False, sort_keys=True).encode()
        report["workload_sha256"] = hashlib.sha256(serialized).hexdigest()
        (output / "synthetic-workload.json").write_bytes(serialized)
        # 预热使用独立查询，避免共享Embedding文本缓存将正式样本变成热缓存。
        warmups = [{**case, "query": "warmup-only:"+case["query"]} for case in (cases[0], cases[-1])]
        report["warmups"] = [run_case(case, args.scheduled,args.whole_request,services) for case in warmups]
        report["groups"] = []
        half = args.count // 2
        for concurrency, group in [(1, cases[:half]), (10, cases[half:])]:
            rows = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [pool.submit(run_case, case, args.scheduled,args.whole_request,services) for case in group]
                for future in concurrent.futures.as_completed(futures):
                    rows.append(future.result())
                    if len(rows) % 10 == 0:
                        print(f"concurrency={concurrency} finished={len(rows)}/{len(group)} failed={sum(r['status']!='PASS' for r in rows)}", flush=True)
            summary = summarize(rows, len(group))
            summary["busy_rejections"] = sum(r["status"] == "BUSY" for r in rows)
            summary["busy_after_partial_model_dispatch"] = sum(r["status"]=="BUSY" and any(e["dispatched"] for e in r.get("local_inference",[])) for r in rows)
            summary["logical_model_dispatches"] = sum(e["dispatched"] for r in rows for e in r.get("local_inference", []))
            batch_events = [e for r in rows for e in r.get("local_inference", []) if e["purpose"]=="embedding_batch"]
            summary["embedding_batches_observed"] = len({e["batch_id"] for e in batch_events})
            summary["merged_embedding_batches_observed"] = len({e["batch_id"] for e in batch_events if e["requests"]>1})
            summary["dispatch_count_scope"] = "logical stages, not physical HTTP attempts; shared batch events counted separately"
            summary["queue_p95_ms"] = percentile([e["queue_ms"] for r in rows for e in r.get("local_inference", [])])
            summary["inference_p95_ms"] = percentile([e["inference_ms"] for r in rows for e in r.get("local_inference", []) if e["inference_ms"] is not None])
            report["groups"].append({"concurrency": concurrency, "summary": summary, "rows": rows})
            (output / f"concurrency-{concurrency}.json").write_text(json.dumps(report["groups"][-1], indent=2) + "\n", encoding="utf-8")
        report["status"] = "PASS_FINITE_PROVIDER_LOAD_ONLY" if all(g["summary"]["status"] == "PASS_FINITE_LOAD_ONLY" for g in report["groups"]) else "FAIL"
    except Exception as exc:
        report["status"], report["error_type"] = "FAIL", type(exc).__name__
    finally:
        try:
            resources.close()
        except Exception as exc:
            report["status"], report["cleanup_error_type"] = "FAIL", type(exc).__name__
        stop.set()
        sampler.join(timeout=3)
        (output / "memory-samples.json").write_text(json.dumps(memory, indent=2), encoding="utf-8")
        after = snapshot_sources(ROOT)
        report.update(compare_sources(before, after))
        (output / "source-end.json").write_text(json.dumps(after, indent=2), encoding="utf-8")
        if not report["source_tree_stable"] or not report["source_snapshot_complete"]:
            report["status"] = "FAIL_SOURCE_DRIFT"
        report["limitations"] = ["distinct load requests repeat12 semantic clusters, not200 independent quality examples",
                                "model service/provider load, not REST/WS full app or business default integration",
                                "HTTP read timeout is not a hard wall-clock cancel; all measured delays retained",
                                "memory sampled, subsecond peaks and other GPU processes not attributable",
                                "no production index writes/deletes or route switch"]
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output / "report.json"),
                      "error_type": report.get("error_type")}), flush=True)
    return 0 if report["status"] == "PASS_FINITE_PROVIDER_LOAD_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
