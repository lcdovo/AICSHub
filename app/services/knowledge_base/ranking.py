"""Production ranking primitives shared by versioned retrieval and focused tests."""
from __future__ import annotations


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding_dimension_mismatch")
    denominator = sum(value * value for value in left) ** .5 * sum(value * value for value in right) ** .5
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.


def fuse_candidates(dense: list[dict], lexical: list[dict], rrf_k: int) -> list[dict]:
    """Rank all eligible candidates before the existing rerank/evidence budgets apply."""
    merged = {}
    for route in (dense, lexical):
        for rank, candidate in enumerate(route, 1):
            item = merged.setdefault(candidate["id"], {**candidate, "rrf_score": 0., "score_type": "rrf"})
            item["rrf_score"] += 1 / (rrf_k + rank)
            for field in ("dense_score", "vector_score", "bm25_score"):
                if field in candidate:
                    item[field] = candidate[field]
    candidates = sorted(merged.values(), key=lambda item: item["rrf_score"], reverse=True)
    for candidate in candidates:
        candidate.update(final_score=candidate["rrf_score"], score_type="rrf")
    return candidates
