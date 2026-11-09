"""Bounded local-only parameter comparison; no chat provider or business writes."""
from __future__ import annotations

from datetime import datetime
import concurrent.futures
import math
import threading
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "reports/parameter-candidates-20261108" / datetime.now().strftime("%H%M%S-%f")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    # Local dependencies only. Keep Windows event-loop self connections available.
    connect = socket.socket.connect
    def local_connect(sock, address):
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError("non_loopback_network_blocked")
        return connect(sock, address)
    socket.socket.connect = local_connect
    from app.config.config import settings
    from app.services.embedding_service import APIEmbedding
    from app.services.reranker_service import RerankerService
    from app.services.local_admission import request_scope
    from app.services.local_inference import inference_scope
    settings.REDIS_HOST = "127.0.0.1"
    settings.REDIS_PORT = 6380
    settings.LOCAL_MODEL_COORDINATOR = "redis"
    settings.LOCAL_MODEL_MAX_CONCURRENCY = 1
    report = {"scope": "local_model_synthetic_only", "provider_chat_calls": 0,
              "business_writes": False, "embedding": [], "rerank": []}
    def save():
        (OUTPUT / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    baseline_code = "from app.config.config import settings as s; import json; names=" + repr([
        "LLM_MAX_TOKENS", "LLM_REQUEST_TIMEOUT_SECONDS", "LLM_MAX_CONCURRENCY",
        "AGENT_TASK_TIMEOUT_SECONDS", "EMBEDDING_BATCH_SIZE", "RAG_TOP_K",
        "RERANKER_LOCAL_BATCH_SIZE", "LOCAL_MODEL_MAX_CONCURRENCY"]) + "; print(json.dumps({k:getattr(s,k,'ABSENT') for k in names}))"
    baseline = subprocess.run(["docker", "compose", "exec", "-T", "app", "python", "-c", baseline_code],
                              cwd=ROOT, capture_output=True, text=True, timeout=30)
    if baseline.returncode:
        raise RuntimeError("baseline_read_failed")
    report["deployed_settings"] = json.loads(baseline.stdout)
    report["source_hashes"] = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in [
        "app/services/embedding_service.py", "app/services/reranker_service.py", "app/services/local_admission.py"]}
    emb = APIEmbedding("http://127.0.0.1:8012/v1", "local-only", "qwen3-embedding:0.6b",
                       dim=1024, timeout=10, document_timeout=60, max_retries=0, batch_size=1)
    rerank = RerankerService(local_base="http://127.0.0.1:7997")
    try:
        with inference_scope(), request_scope():
            emb.encode_query("合成参数对照预热")
        for chars in (500, 1500):
            unit = "合成条款：质量问题需核实，保留配件及包装；缺少条件时补问，不承诺退款结果。"
            texts = [(unit * (chars // len(unit) + 1))[:chars] + f"编号{i}" for i in range(8)]
            reference = None
            captures = {}
            for variant, batch in (("A1", 1), ("B", 8), ("A2", 1)):
                emb._text_cache.clear()
                emb.batch_size = batch
                events = []
                began = time.perf_counter()
                row = {"variant": variant, "batch": batch, "chars_per_text": len(texts[0]), "texts": 8}
                try:
                    with inference_scope(priority="background", events=events), request_scope(purpose="index_batch"):
                        vectors = emb.encode_batch(texts)
                    if len(vectors) != 8 or any(len(v) != 1024 for v in vectors):
                        raise ValueError("incomplete_embedding")
                    captures[variant] = vectors
                    row["status"] = "ok"
                    if reference is None:
                        reference = vectors
                    row["max_vector_delta"] = max(abs(x-y) for a,b in zip(reference,vectors) for x,y in zip(a,b))
                except Exception as exc:
                    row.update(status="failed", error_type=type(exc).__name__)
                row.update(duration_ms=round((time.perf_counter()-began)*1000, 2), events=events)
                report["embedding"].append(row)
                save()
            if all(k in captures for k in ("A1", "B", "A2")):
                def cosine(a, b):
                    return sum(x*y for x,y in zip(a,b)) / math.sqrt(sum(x*x for x in a)*sum(y*y for y in b))
                report.setdefault("embedding_similarity", []).append({"chars": chars,
                    "batch8_vs_final_batch1_min_cosine": min(cosine(a,b) for a,b in zip(captures["B"], captures["A2"])),
                    "initial_vs_final_batch1_min_cosine": min(cosine(a,b) for a,b in zip(captures["A1"], captures["A2"]))})
                save()
        for variant, batch in (("A1", 1), ("B", 8), ("A2", 1)):
            for repeat in range(3):
                emb.batch_size = batch
                emb._text_cache.clear()
                acquired = threading.Event()
                def background():
                    events = []
                    began = time.perf_counter()
                    try:
                        with inference_scope(priority="background", events=events), request_scope(purpose="index_batch"):
                            acquired.set()
                            vectors = emb.encode_batch([("合成长文：保留包装及配件，质量问题需核实。" * 70) + f"{repeat}-{i}" for i in range(8)])
                        return {"status":"ok", "vectors":len(vectors), "duration_ms":(time.perf_counter()-began)*1000}
                    except Exception as exc:
                        acquired.set()
                        return {"status":"failed", "error_type":type(exc).__name__, "duration_ms":(time.perf_counter()-began)*1000}
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(background)
                    if not acquired.wait(12):
                        raise RuntimeError("background_start_timeout")
                    began = time.perf_counter()
                    events = []
                    try:
                        with inference_scope(events=events), request_scope(purpose="interactive_probe"):
                            vector = emb.encode_query(f"竞争样本{variant}-{repeat}包装要求")
                        query_result = {"status":"ok", "dimension":len(vector)}
                    except Exception as exc:
                        query_result = {"status":"failed", "error_type":type(exc).__name__}
                    query_result.update(duration_ms=(time.perf_counter()-began)*1000, events=events)
                    result = {"variant":variant, "batch":batch, "repeat":repeat,
                              "background":future.result(), "query":query_result}
                    report.setdefault("contention", []).append(result)
                    save()
                    if result["background"]["status"] != "ok" or query_result["status"] != "ok":
                        break
        corpus = json.loads((ROOT / "acceptance/m5/corpus.json").read_bytes())
        docs = [{"id": str(i), "content": (corpus[i % len(corpus)]["content"] + f"合成编号{i}。") * 4}
                for i in range(20)]
        query = "质量问题退货的包装、配件和运费条件是什么？"
        reference = None
        for variant, batch in (("A1", 4), ("B", 20), ("A2", 4)):
            settings.RERANKER_LOCAL_BATCH_SIZE = batch
            for repeat in range(3):
                events = []
                began = time.perf_counter()
                row = {"variant": variant, "batch": batch, "repeat": repeat, "candidates": 20}
                try:
                    with inference_scope(events=events), request_scope():
                        ranked = rerank.rerank_sync(query, docs, 20)
                    if len(ranked) != 20 or rerank.last_used_provider != "local":
                        raise ValueError("rerank_failed_or_degraded")
                    scores = {r["id"]: r["rerank_score"] for r in ranked}
                    order = [r["id"] for r in ranked]
                    if reference is None:
                        reference = (scores, order)
                    row.update(status="ok", ranking=order, same_ranking=order == reference[1],
                        max_score_delta=max(abs(scores[k]-reference[0][k]) for k in scores),
                        top3=[r["id"] for r in ranked[:3]], top5=[r["id"] for r in ranked[:5]],
                        top3_chars=sum(len(r["content"]) for r in ranked[:3]),
                        top5_chars=sum(len(r["content"]) for r in ranked[:5]))
                except Exception as exc:
                    row.update(status="failed", error_type=type(exc).__name__)
                row.update(duration_ms=round((time.perf_counter()-began)*1000,2), events=events,
                           dispatches=sum(bool(e.get("dispatched")) for e in events))
                report["rerank"].append(row)
                save()
                if row["status"] != "ok":
                    break
    finally:
        emb.close()
        rerank.close()
        save()
    print(json.dumps({"report": str(OUTPUT / "comparison.json"),
        "embedding": [{k:v for k,v in r.items() if k != "events"} for r in report["embedding"]],
        "rerank": [{k:v for k,v in r.items() if k not in ("events", "ranking")} for r in report["rerank"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
