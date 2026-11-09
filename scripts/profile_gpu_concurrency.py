"""Bounded synthetic local GPU probe; no provider keys or business writes.

Direct endpoint concurrency deliberately bypasses application admission for measurement.
Queue-inclusive latency and successful throughput are reported separately.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
import time

import httpx


def percentile(values: list[float], q: float = .95) -> float | None:
    values = sorted(values)
    return round(values[max(0, math.ceil(len(values) * q) - 1)], 3) if values else None


async def probe(count: int) -> dict:
    queries = [f"合成测试{i}：质量问题退货的包装和运费要求" for i in range(count)]
    documents = [f"合成政策{j}：质量问题退货需要保留产品、配件和原包装，核实后商家承担运费。" * 4 for j in range(20)]
    groups = []
    baseline = {}
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        async def request(query):
            embedding = (await client.post("http://127.0.0.1:8012/v1/embeddings", json={
                "model": "qwen3-embedding:0.6b", "input": [query], "dimensions": 1024,
                "encoding_format": "float"})).raise_for_status().json()
            if len(embedding["data"]) != 1 or len(embedding["data"][0]["embedding"]) != 1024:
                raise ValueError("embedding_shape")
            ranked = (await client.post("http://127.0.0.1:7997/rerank", json={
                "model": "BAAI/bge-reranker-large", "query": query, "documents": documents,
                "top_n": 20, "return_documents": False})).raise_for_status().json()["results"]
            if len(ranked) != 20 or len({r["index"] for r in ranked}) != 20:
                raise ValueError("rerank_shape")
            return {r["index"]: r["relevance_score"] for r in ranked}

        await request(queries[0])  # Explicit warmup, excluded from measured groups.
        for capacity in (1, 2, 4, 1):
            semaphore = asyncio.Semaphore(capacity)
            started = time.perf_counter()

            async def one(index, query):
                queued = time.perf_counter()
                async with semaphore:
                    entered = time.perf_counter()
                    try:
                        scores = await request(query)
                        delta = max(abs(value - baseline[index][key]) for key, value in scores.items()) if index in baseline else 0
                        baseline.setdefault(index, scores)
                        return {"index": index, "status": "ok", "service_ms": (time.perf_counter()-entered)*1000,
                            "total_ms": (time.perf_counter()-queued)*1000, "score_max_delta": delta}
                    except Exception as exc:
                        return {"index": index, "status": "failed", "error_type": type(exc).__name__,
                            "total_ms": (time.perf_counter()-queued)*1000}

            rows = await asyncio.gather(*(one(i, q) for i, q in enumerate(queries)))
            elapsed = time.perf_counter()-started
            ok = [row for row in rows if row["status"] == "ok"]
            groups.append({"concurrency": capacity, "count": count, "passed": len(ok),
                "duration_s": elapsed, "success_per_second": len(ok)/elapsed,
                "service_p95_ms": percentile([r["service_ms"] for r in ok]),
                "queue_inclusive_p95_ms": percentile([r["total_ms"] for r in rows]), "rows": rows})
    return {"scope": "synthetic warm local embedding + rerank, closed burst; not API or fixed-arrival capacity",
        "external_paid_calls": 0, "warmup_chains": 1, "groups": groups}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, choices=range(10, 25), default=12)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.count))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps([{k:v for k,v in g.items() if k != "rows"} for g in result["groups"]]))


if __name__ == "__main__":
    main()
