"""每次检索取SQL发布快照；双路独立，版本/指纹过滤先于排名与缓存。"""

import asyncio
from contextvars import copy_context
from contextlib import nullcontext
import threading
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import time
from .bm25 import BM25Scorer, BM25_VERSION  # noqa: F401 - compatibility patch target
from .lifecycle import embedding_identity
from .ranking import cosine_similarity, fuse_candidates
from .retrieval_text import TEXT_POLICY_VERSION
from .evidence_assembly import ASSEMBLY_VERSION, assemble_evidence
from .product_scope import VERSION as PRODUCT_SCOPE_VERSION, scope_candidates
from app.config.config import settings


_PROVIDER_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="m3-retrieval")
_PROVIDER_SLOTS = threading.BoundedSemaphore(4)
ENCODE_TIMEOUT_SECONDS = 5


def bounded_provider(call):
    if not _PROVIDER_SLOTS.acquire(blocking=False):
        raise RuntimeError("provider_budget_exhausted")
    try:
        from app.services.local_inference import inference_scope
        context = copy_context()
        deadline = time.monotonic() + ENCODE_TIMEOUT_SECONDS
        def scoped_call():
            with inference_scope(deadline=deadline):
                return call()
        future = _PROVIDER_POOL.submit(context.run, scoped_call)
    except BaseException:
        _PROVIDER_SLOTS.release()
        raise
    future.add_done_callback(lambda _: _PROVIDER_SLOTS.release())
    return future.result(timeout=ENCODE_TIMEOUT_SECONDS)


def search(
    service, query, top_k=None, similarity_threshold=None, recall_k=None, rerank_k=None, retrieval_mode="hybrid"
):
    from app.services.local_inference import inference_scope
    observations = []
    started = time.perf_counter()
    with inference_scope(priority="interactive", events=observations):
        try:
            result = _search(service, query, top_k, similarity_threshold, recall_k, rerank_k, retrieval_mode)
        except Exception as exc:
            from app.services.local_inference import LocalModelBusy, LocalModelDeadline
            if not isinstance(exc,(LocalModelBusy,LocalModelDeadline)):
                raise
            reason = "local_model_busy" if isinstance(exc,LocalModelBusy) else "local_model_deadline"
            elapsed = round((time.perf_counter()-started)*1000,3)
            meta = {"score_type":"rrf","knowledge_epoch":None,"degraded":True,
                    "degraded_reason":reason,"latency_ms":elapsed,"cache":"miss"}
            result = {"success":True,"results":[],"meta":meta,"degraded":True,
                      "degraded_reason":reason,"from_cache":False,"latency_ms":elapsed,
                      "execution_time_ms":elapsed,"search_strategy":"empty",
                      "evidence_bundle":{"schema_version":1,"knowledge_epoch":None,"items":[],"support":"unknown"}}
    result["meta"]["local_inference"] = list(observations)
    reason = result.get("degraded_reason") or ""
    result["meta"]["local_model_admission"] = (
        "busy" if any(e["status"] == "busy" for e in observations) or "local_model_busy" in reason else
        "deadline" if any(e["status"] == "deadline" for e in observations) or "local_model_deadline" in reason else
        "cache" if result.get("from_cache") else "completed_or_not_local")
    result["meta"]["local_model_scheduler_scope"] = settings.LOCAL_MODEL_COORDINATOR
    result["meta"]["local_model_max_concurrency"] = settings.LOCAL_MODEL_MAX_CONCURRENCY
    result["meta"]["local_rerank_batch_size"] = settings.RERANKER_LOCAL_BATCH_SIZE
    result["meta"]["local_request_admission"] = [e for e in observations if e.get("purpose")=="retrieval"]
    return result


def _search(
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
    epoch, chunks, snapshot_hit = service._retrieval_runtime.snapshot(service._repository)
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
            "RERANKER_LOCAL_BATCH_SIZE",
            "RERANKER_API_STYLE",
            "RERANKER_TIMEOUT_SECONDS",
            "RAG_USE_RERANKER",
        )
    }
    from app.services.reranker_service import RERANK_ALGORITHM_VERSION
    rerank_identity["algorithm_version"] = RERANK_ALGORITHM_VERSION
    rerank_identity["phase_timeout_seconds"] = ENCODE_TIMEOUT_SECONDS
    from app.services.local_inference import SCHEDULER_VERSION
    from app.services.local_admission import VERSION
    rerank_identity["local_model_scheduler"] = SCHEDULER_VERSION + VERSION + settings.LOCAL_MODEL_COORDINATOR
    rerank_identity["local_model_max_concurrency"] = settings.LOCAL_MODEL_MAX_CONCURRENCY
    cache_key = hashlib.sha256(
        json.dumps(
            [
                "structured-search-v3",
                BM25_VERSION, TEXT_POLICY_VERSION, ASSEMBLY_VERSION, PRODUCT_SCOPE_VERSION,
                settings.RAG_RRF_K, settings.RAG_EVIDENCE_MAX_CHARS, settings.RAG_CONTEXT_NEIGHBORS,
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
        result["meta"]["snapshot_cache"] = "hit" if snapshot_hit else "miss"
        result["meta"]["lexical_index_cache"] = "unused"
        result["meta"]["timings_ms"] = {
            "snapshot_and_cache": result["latency_ms"],
            "encode": 0,
            "dense": 0,
            "bm25": 0,
            "fusion": 0,
            "rerank": 0,
            "assembly": 0,
        }
        return result
    from app.services.local_admission import request_scope, uses_local_models
    local = uses_local_models(
        service._embedding_service if retrieval_mode != "bm25" else None,
        service._reranker if settings.RAG_USE_RERANKER else None)
    with (request_scope(purpose="retrieval") if local else nullcontext()) as permit:
        timings = {"snapshot": snapshot_ms}
        errors = []
        by_id = {c["id"]: c for c in chunks}
        begin = time.perf_counter()
        lexical = []
        lexical_hit = False
        if retrieval_mode != "dense":
            bm25, lexical_hit = service._retrieval_runtime.lexical(epoch, chunks)
            lexical = [{**chunks[i], "bm25_score": score, "score_type": "bm25"}
                       for i, score in bm25.search(query, recall_k) if score > 0]
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
            except Exception as exc:
                from app.services.local_inference import LocalModelBusy, LocalModelDeadline
                vector = None
                errors.append("local_model_busy" if isinstance(exc, LocalModelBusy) else
                              "local_model_deadline" if isinstance(exc, (LocalModelDeadline, TimeoutError)) else "embedding_unavailable_or_invalid")
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
                        score = cosine_similarity(vector, stored)
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
        candidates = fuse_candidates(dense, lexical, settings.RAG_RRF_K)
        candidates, product_scope = scope_candidates(query, candidates)
        fused_by_id = {candidate["id"]: candidate for candidate in candidates}
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
                    return bounded(query, copy.deepcopy(selected), len(selected), deadline=rerank_deadline)
                return service._reranker.rerank_sync(query, copy.deepcopy(selected), len(selected))

            try:
                reranked = bounded_provider(rerank_current)
            except Exception as exc:
                from app.services.local_inference import LocalModelBusy, LocalModelDeadline
                reranked = None
                if isinstance(exc, (LocalModelBusy, LocalModelDeadline, TimeoutError)):
                    errors.append("local_model_busy" if isinstance(exc, LocalModelBusy) else "local_model_deadline")
            if (
                reranked is not None
                and len(reranked) == len(selected)
                and len({c.get("id") for c in reranked}) == len(reranked)
                and all(c.get("id") in {item["id"] for item in selected} for c in reranked)
            ):
                candidates = [
                    {
                        **fused_by_id[c["id"]],
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
        begin = time.perf_counter()
        results, assembly = assemble_evidence(candidates, chunks, top_k=top_k,
            max_chars=settings.RAG_EVIDENCE_MAX_CHARS, neighbors=settings.RAG_CONTEXT_NEIGHBORS)
        for item in results:
            item["index_epoch"] = epoch
        timings["assembly"] = round((time.perf_counter() - begin) * 1000, 3)
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
            "snapshot_cache": "hit" if snapshot_hit else "miss",
            "lexical_index_cache": "hit" if lexical_hit else "miss" if retrieval_mode != "dense" else "unused",
            "bm25_version": BM25_VERSION,
            "evidence_assembly": assembly,
            "product_scope": product_scope,
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
            "total_candidates": len(fused_by_id),
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
        if permit is not None:
            permit.check()
        if not any(code in errors for code in ("local_model_busy", "local_model_deadline")):
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
