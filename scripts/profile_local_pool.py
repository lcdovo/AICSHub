"""Fixed-arrival comparison of real local model chains and shared Redis leases."""
from __future__ import annotations
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, choices=range(20, 41), default=24)
    parser.add_argument("--batch-size", type=int, choices=(4, 20), default=4)
    args = parser.parse_args()
    import redis
    from app.services.embedding_service import APIEmbedding
    from app.services.reranker_service import RerankerService
    from app.services.local_admission import RedisCoordinator, request_scope
    from app.services.local_pool import RedisPoolCoordinator
    from app.services.local_inference import inference_scope, LocalModelBusy, LocalModelDeadline
    from app.config.config import settings
    settings.RERANKER_LOCAL_BATCH_SIZE = args.batch_size
    from scripts.evaluate_gpu_arrivals import run_arrivals
    from scripts.profile_gpu_concurrency import percentile
    client = redis.Redis(host="127.0.0.1", port=6380, socket_timeout=.3)
    embedding = APIEmbedding("http://127.0.0.1:8012/v1", "local-only", "qwen3-embedding:0.6b", dim=1024, max_retries=0, batch_size=1)
    reranker = RerankerService(local_base="http://127.0.0.1:7997", local_model="BAAI/bge-reranker-large")
    groups = []
    namespace = "aicshub:test:profile-pool:{" + uuid.uuid4().hex + "}"
    documents = [{"id": str(i), "content": f"合成政策{i}：质量问题退货请保留产品、配件和包装，核实后商家承担运费。" * 4} for i in range(20)]
    def call(case):
        events = []
        try:
            with inference_scope(events=events):
                with request_scope():
                    vector = embedding.encode_query(case["query"])
                    ranked = reranker.rerank_sync(case["query"], documents, 20)
                    if len(vector) != 1024 or len(ranked) != 20 or reranker.last_used_provider != "local":
                        raise ValueError("local_result_or_provider_invalid")
            return {"status": "ok", "local_inference": events}
        except (LocalModelBusy, LocalModelDeadline) as exc:
            return {"status": type(exc).__name__, "local_inference": events}
        except Exception as exc:
            return {"status": "failed", "error_type": type(exc).__name__, "local_inference": events}
    try:
        for capacity in (1, 2, 1, 2):
            coord = RedisCoordinator(client, namespace) if capacity == 1 else RedisPoolCoordinator(client, namespace)
            with patch("app.services.local_admission.get_coordinator", lambda: coord):
                warm = call({"query": "合成预热退货政策"})
                if warm["status"] != "ok":
                    raise RuntimeError("local_probe_warmup_failed:" + warm["status"])
                for rate in (3, 4):
                    cases = [{"case_id": str(i), "query": f"合成样本{capacity}-{len(groups)}-{i}质量问题退货包装要求"} for i in range(args.count)]
                    rows, timing = run_arrivals(cases, rate, call, max_inflight=8)
                    ok = [r for r in rows if r["status"] == "ok"]
                    groups.append({"capacity": capacity, "batch_size":args.batch_size, "rate": rate, "count": len(rows), "passed": len(ok),
                        "success_per_second": len(ok)/timing["duration_s"],
                        "all_attempts_p95_ms": percentile([r["from_planned_ms"] for r in rows]),
                        "success_p95_ms": percentile([r["from_planned_ms"] for r in ok]),
                        "submit_lag_p95_ms": percentile([r["submit_lag_ms"] for r in rows]),
                        "queue_p95_ms": percentile([e["queue_ms"] for r in rows for e in r["local_inference"] if e.get("admission_scope")]),
                        "timing": timing, "rows": rows})
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps({"scope": "finite synthetic fixed-arrival local model chain, not API capacity", "paid_calls": 0, "groups": groups}, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(json.dumps({k:v for k,v in groups[-1].items() if k not in {"rows", "timing"}}), flush=True)
    finally:
        embedding.close(); reranker.close()
        keys = list(client.scan_iter(namespace + "*"))
        if keys: client.delete(*keys)
        client.close()


if __name__ == "__main__": main()
