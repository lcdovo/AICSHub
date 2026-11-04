"""每次检索取SQL发布快照；双路独立，版本/指纹过滤先于排名与缓存。"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import time
from .bm25 import BM25Scorer
from .lifecycle import embedding_identity
from app.config.config import settings


_PROVIDER_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="m3-retrieval")
_PROVIDER_SLOTS = threading.BoundedSemaphore(4)
ENCODE_TIMEOUT_SECONDS = 5


def bounded_provider(call):
    if not _PROVIDER_SLOTS.acquire(blocking=False):
        raise RuntimeError("provider_budget_exhausted")
    try:
        future = _PROVIDER_POOL.submit(call)
    except BaseException:
        _PROVIDER_SLOTS.release()
        raise
    future.add_done_callback(lambda _: _PROVIDER_SLOTS.release())
    return future.result(timeout=ENCODE_TIMEOUT_SECONDS)


def search(
    service, query, top_k=None, similarity_threshold=None, recall_k=None, rerank_k=None, retrieval_mode="hybrid"
):
    if retrieval_mode not in {"hybrid", "bm25", "dense"}:
        raise ValueError("invalid_retrieval_mode")
    started = time.perf_counter()
    service.initialize()
    top_k = max(1, min(int(top_k or settings.RAG_TOP_K), 50))
    recall_k = max(top_k, min(int(recall_k or settings.RAG_RECALL_K), 200))
    rerank_k = max(top_k, min(int(rerank_k or settings.RAG_RERANK_K), 200))
    threshold = (
        settings.RAG_SIMILARITY_THRESHOLD
        if similarity_threshold is None
        else float(similarity_threshold)
    )
    epoch, chunks = service._repository.snapshot()
    snapshot_ms = round((time.perf_counter() - started) * 1000, 3)
    # 当前知识产品为所有已认证用户可读、仅管理员写。没有未实现的文档ACL绕过。
    fingerprint, identity = embedding_identity(service._embedding_service)
    rerank_identity = {
        k: getattr(settings, k, None)
        for k in (
            "RERANKER_API_BASE",
            "RERANKER_MODEL",
            "RERANKER_LOCAL_BASE",
            "RERANKER_LOCAL_MODEL",
            "RERANKER_API_STYLE",
            "RERANKER_TIMEOUT_SECONDS",
            "RAG_USE_RERANKER",
        )
    }
    from app.services.reranker_service import RERANK_ALGORITHM_VERSION
    rerank_identity["algorithm_version"] = RERANK_ALGORITHM_VERSION
    rerank_identity["phase_timeout_seconds"] = ENCODE_TIMEOUT_SECONDS
    cache_key = hashlib.sha256(
        json.dumps(
            [
                "m5-search-v2",
                retrieval_mode,
                query,
                epoch,
                fingerprint,
                rerank_identity,
                top_k,
                recall_k,
                rerank_k,
                threshold,
                "authenticated-published",
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    cached = service._query_cache.get(cache_key)
    if cached:
        result = copy.deepcopy(cached)
        result["from_cache"] = True
        result["latency_ms"] = result["execution_time_ms"] = round(
            (time.perf_counter() - started) * 1000, 3
        )
        result["meta"]["latency_ms"] = result["latency_ms"]
        result["meta"]["cache"] = "hit"
        result["meta"]["timings_ms"] = {
            "snapshot_and_cache": result["latency_ms"],
            "encode": 0,
            "dense": 0,
            "bm25": 0,
            "fusion": 0,
            "rerank": 0,
        }
        return result
    timings = {"snapshot": snapshot_ms}
    errors = []
    by_id = {c["id"]: c for c in chunks}
    begin = time.perf_counter()
    lexical = []
    if retrieval_mode != "dense":
        bm25 = BM25Scorer()
        bm25.build_index([c["content"] for c in chunks]) if chunks else None
        for i, c in enumerate(chunks):
            score = bm25.score(query, i)
            if score > 0:
                lexical.append({**c, "bm25_score": score, "score_type": "bm25"})
        lexical = sorted(lexical, key=lambda c: c["bm25_score"], reverse=True)[:recall_k]
    timings["bm25"] = round((time.perf_counter() - begin) * 1000, 3)
    dense = []
    begin = time.perf_counter()
    vector = None
    if retrieval_mode != "bm25":
        try:
            vector = bounded_provider(
                lambda: service._embedding_service.encode_query(query)
            )
            from app.services.embedding_service import validate_vector

            vector = validate_vector(vector, identity["dim"])
        except Exception:
            vector = None
            errors.append("embedding_unavailable_or_invalid")
    timings["encode"] = round((time.perf_counter() - begin) * 1000, 3)
    begin = time.perf_counter()
    dense_backend = "none"
    matching = [c for c in chunks if c["embedding_fingerprint"] == fingerprint]
    if retrieval_mode != "bm25" and len(matching) != len(chunks):
        errors.append("embedding_fingerprint_mismatch")
    if vector is not None:
        if service._vector_store:
            try:
                raw = bounded_provider(
                    lambda: service._vector_store.search(
                        vector, fingerprint, [c["id"] for c in matching], recall_k
                    )
                )
                dense = [
                    {
                        **by_id[r["id"]],
                        "dense_score": r["dense_score"],
                        "vector_score": r["dense_score"],
                        "score_type": "cosine",
                    }
                    for r in raw
                    if r["id"] in by_id and r["dense_score"] >= threshold
                ]
                dense_backend = "milvus"
            except Exception:
                errors.append("milvus_unavailable_or_index_missing")
        if dense_backend != "milvus":
            dense_backend = "sql_vectors"
            for c in matching:
                try:
                    stored = validate_vector(c["_embedding"], identity["dim"])
                    score = service._cosine_similarity(vector, stored)
                    if score >= threshold:
                        dense.append(
                            {
                                **c,
                                "dense_score": score,
                                "vector_score": score,
                                "score_type": "cosine",
                            }
                        )
                except (ValueError, TypeError):
                    errors.append("persisted_vector_invalid")
            dense = sorted(dense, key=lambda c: c["dense_score"], reverse=True)[
                :recall_k
            ]
    timings["dense"] = round((time.perf_counter() - begin) * 1000, 3)
    begin = time.perf_counter()
    merged = {}
    for route in (dense, lexical):
        for rank, c in enumerate(route, 1):
            item = merged.setdefault(
                c["id"], {**c, "rrf_score": 0.0, "score_type": "rrf"}
            )
            item["rrf_score"] += 1 / (60 + rank)
            for score_key in ("dense_score", "vector_score", "bm25_score"):
                if score_key in c:
                    item[score_key] = c[score_key]
    candidates = sorted(merged.values(), key=lambda c: c["rrf_score"], reverse=True)
    for c in candidates:
        c["final_score"] = c["rrf_score"]
        c["score_type"] = "rrf"
    timings["fusion"] = round((time.perf_counter() - begin) * 1000, 3)
    begin = time.perf_counter()
    reranker = "none"
    rerank_candidate_count = 0
    if (
        settings.RAG_USE_RERANKER
        and service._reranker is not None
        and service._reranker.is_available()
    ):
        selected = candidates[:rerank_k]
        rerank_candidate_count = len(selected)
        rerank_deadline = time.monotonic() + ENCODE_TIMEOUT_SECONDS

        def rerank_current():
            bounded = getattr(service._reranker, "rerank_with_deadline", None)
            if callable(bounded):
                return bounded(query, copy.deepcopy(selected), top_k, deadline=rerank_deadline)
            return service._reranker.rerank_sync(query, copy.deepcopy(selected), top_k)

        try:
            reranked = bounded_provider(rerank_current)
        except Exception:
            reranked = None
        if (
            reranked is not None
            and len(reranked) == min(top_k, len(selected))
            and len({c.get("id") for c in reranked}) == len(reranked)
            and all(c.get("id") in {item["id"] for item in selected} for c in reranked)
        ):
            candidates = [
                {
                    **merged[c["id"]],
                    "rerank_score": c.get("rerank_score", c.get("final_score")),
                }
                for c in reranked
            ]
            reranker = (
                (reranked[0].get("rerank_provider") if reranked else None)
                or service._reranker.last_used_provider
                or "model"
            )
        else:
            errors.append("reranker_failed_or_invalid")
            reranker = "rank_preserved"
    timings["rerank"] = round((time.perf_counter() - begin) * 1000, 3)
    results = []
    for c in candidates[:top_k]:
        item = {k: v for k, v in c.items() if k != "_embedding"}
        item["index_epoch"] = epoch
        context = item.get("source_context") or {}
        item["evidence_text"] = "\n".join(
            filter(
                None,
                [
                    context.get("heading"),
                    context.get("faq_question"),
                    context.get("table_header"),
                    item["content"],
                ],
            )
        )
        results.append(item)
    # 完整有界chunk进入EvidenceBundle，保留原文位置；覆盖校准留M5，不把排名当概率。
    total = round((time.perf_counter() - started) * 1000, 3)
    meta = {
        "backend": dense_backend,
        "degraded": bool(errors) or (retrieval_mode != "bm25" and dense_backend != "milvus"),
        "degraded_reason": ";".join(sorted(set(errors)))
        or ("milvus_disabled" if retrieval_mode != "bm25" and dense_backend != "milvus" else None),
        "knowledge_epoch": epoch,
        "retrieval_mode": retrieval_mode,
        "embedding_fingerprint": fingerprint,
        "score_type": "rrf",
        "reranker": reranker,
        "rerank_candidate_count": rerank_candidate_count,
        "rerank_budget_ms": ENCODE_TIMEOUT_SECONDS * 1000,
        "rerank_algorithm_version": RERANK_ALGORITHM_VERSION,
        "cache": "miss",
        "timings_ms": timings,
        "latency_ms": total,
        "routes": {
            "dense": {"ids": [c["id"] for c in dense], "backend": dense_backend},
            "bm25": {"ids": [c["id"] for c in lexical], "backend": "sql_snapshot_bm25"},
        },
        "recall_k": recall_k,
        "rerank_k": rerank_k,
        "evidence_k": top_k,
    }
    result = {
        "success": True,
        "query": query,
        "results": results,
        "total_candidates": len(merged),
        "search_strategy": "hybrid"
        if dense and lexical
        else "vector"
        if dense
        else "bm25"
        if lexical
        else "empty",
        "from_cache": False,
        "meta": meta,
        "latency_ms": total,
        "execution_time_ms": total,
        "backend": meta["backend"],
        "degraded": meta["degraded"],
        "degraded_reason": meta["degraded_reason"],
        "reranker": reranker,
        "evidence_bundle": {
            "schema_version": 1,
            "knowledge_epoch": epoch,
            "items": results,
            "support": "unknown",
        },
    }
    service._query_cache.set(cache_key, copy.deepcopy(result))
    return result


class VersionedSearchMixin:
    def search(self, query, top_k=None, similarity_threshold=None, **kwargs):
        return search(self, query, top_k, similarity_threshold, **kwargs)

    async def async_search(self, *a, **kw):
        return await asyncio.to_thread(self.search, *a, **kw)

    def evidence_is_current(self, items):
        self.initialize()
        epoch, chunks = self._repository.snapshot()
        current = {c["id"]: c for c in chunks}
        return all(
            i.get("id") in current
            and i.get("version_id") == current[i["id"]]["version_id"]
            for i in items
        )
