"""可复现的 RAG 检索评测。

运行：``python scripts/rag_benchmark.py``（默认使用内存 Mock embedding）。
也可在测试中调用 ``run_benchmark(kb, dataset=...)``，不依赖 Milvus 或真实模型。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DATASET = [
    {"query": "签收后多久可以无理由退货", "relevant_titles": ["退换货政策"]},
    {"query": "银行卡退款需要几天到账", "relevant_titles": ["退换货政策"]},
    {"query": "如何查询订单物流", "relevant_titles": ["订单查询说明"]},
    {"query": "如何联系人工客服", "relevant_titles": ["常见问题解答"]},
    {"query": "量子纠缠实验室预约", "relevant_titles": []},
]


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = min(len(values) - 1, max(0, int(round((percentile / 100) * (len(values) - 1)))))
    return round(values[index], 3)


def run_benchmark(kb: Any, dataset: Iterable[dict[str, Any]] | None = None, top_k: int = 3) -> dict[str, Any]:
    """计算 Recall@K、MRR、Precision@K、空召回率与 P50/P95 延迟。"""
    samples = list(dataset or DEFAULT_DATASET)
    if not samples:
        return {"dataset_size": 0, "recall_at_k": 0.0, "mrr": 0.0, "precision_at_k": 0.0, "empty_recall_rate": 0.0, "latency_ms": {"p50": 0.0, "p95": 0.0}}
    recalls: list[float] = []
    precisions: list[float] = []
    reciprocal_ranks: list[float] = []
    empty_hits = 0
    latencies: list[float] = []
    for sample in samples:
        started = time.perf_counter()
        threshold = sample.get("similarity_threshold")
        if threshold is None:
            from app.config.config import settings
            threshold = settings.RAG_SIMILARITY_THRESHOLD
        result = kb.search(sample["query"], top_k=top_k, similarity_threshold=threshold)
        latencies.append((time.perf_counter() - started) * 1000)
        titles = [item.get("title", "") for item in result.get("results", [])[:top_k]]
        relevant = set(sample.get("relevant_titles", []))
        if not relevant:
            empty_hits += int(not titles)
            continue
        hits = sum(title in relevant for title in titles)
        recalls.append(1.0 if hits else 0.0)
        precisions.append(hits / max(top_k, 1))
        rank = next((idx + 1 for idx, title in enumerate(titles) if title in relevant), None)
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
    answerable = max(len(samples) - sum(1 for s in samples if not s.get("relevant_titles")), 1)
    unknown_count = sum(1 for s in samples if not s.get("relevant_titles"))
    return {
        "dataset_size": len(samples),
        "answerable_samples": len(recalls),
        "recall_at_k": round(statistics.mean(recalls), 4) if recalls else 0.0,
        "mrr": round(statistics.mean(reciprocal_ranks), 4) if reciprocal_ranks else 0.0,
        "precision_at_k": round(statistics.mean(precisions), 4) if precisions else 0.0,
        "empty_recall_rate": round(empty_hits / max(unknown_count, 1), 4),
        "latency_ms": {"p50": _percentile(latencies, 50), "p95": _percentile(latencies, 95)},
        "backend": getattr(kb, "backend", "unknown"),
        "degraded": bool(getattr(kb, "degraded", False)),
        "degraded_reason": getattr(kb, "degraded_reason", None) or None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="运行本地 RAG 检索基准")
    parser.add_argument("--dataset", type=Path, help="JSON 数组数据集")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    from app.services.knowledge_base import get_knowledge_base
    dataset = json.loads(args.dataset.read_text(encoding="utf-8")) if args.dataset else DEFAULT_DATASET
    print(json.dumps(run_benchmark(get_knowledge_base(), dataset, args.top_k), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
