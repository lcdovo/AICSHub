"""检索链路：混合检索（Milvus 原生 hybrid / 内存双路降级）、RRF 融合与重排。

自 knowledge_base.py 拆分迁移（原 search / _search_milvus / _merge_results /
_rerank / _heuristic_rescore 等），逻辑保持原样：

- 主链路：Milvus 2.5 原生混合检索（dense + FULLTEXT BM25，服务端 RRF）。
- 降级链路：向量 + 内存 BM25 双路 RRF 融合（milvus 不可用时）。
- 重排：模型 reranker（外部 API → 本地 bge-reranker）→ 启发式回退。
"""
import asyncio
import logging
import re
import time
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from app.config.config import settings

from .bm25 import BM25Scorer
from .cache_invalidation import QueryCache

logger = logging.getLogger(__name__)


class RetrievalMixin:
    """混合检索链路：Milvus 原生 hybrid、内存双路 RRF 融合、模型/启发式重排。"""

    def search(
        self,
        query: str,
        top_k: int = None,
        similarity_threshold: float = None,
    ) -> Dict[str, Any]:
        self.initialize()

        top_k = top_k if top_k is not None else settings.RAG_TOP_K
        similarity_threshold = similarity_threshold if similarity_threshold is not None else settings.RAG_SIMILARITY_THRESHOLD
        top_k = max(1, min(int(top_k), 50))
        similarity_threshold = max(0.0, min(float(similarity_threshold), 1.0))

        cache_key = QueryCache.make_key(query, top_k, similarity_threshold)
        cached = self._query_cache.get(cache_key)
        if cached:
            return {**cached, "from_cache": True}

        started = time.perf_counter()
        if self._vector_store:
            try:
                result = self._search_milvus(
                    query, top_k, similarity_threshold, started, cache_key
                )
                self._query_cache.set(cache_key, result)
                return result
            except Exception as exc:
                # Milvus 检索失败（索引未就绪/超时等）走内存双路降级，保持三级降级承诺。
                logger.error(f"Milvus hybrid search failed, falling back to memory: {exc}")

        # 同步路径：串行执行两路检索（语义等价于原实现）。
        query_embedding = self._encode_query_embedding(query)
        if query_embedding is None:
            return self._embedding_failed_result(query, started)

        vector_results = self._retrieve_vector(query_embedding, query, top_k, similarity_threshold)
        bm25_results = self._retrieve_bm25(query, top_k)
        return self._compose_result(query, top_k, similarity_threshold, vector_results, bm25_results, started, cache_key)

    async def async_search(
        self,
        query: str,
        top_k: int = None,
        similarity_threshold: float = None,
    ) -> Dict[str, Any]:
        """异步检索：embedding 后向量与 BM25 两路并行，结果与同步 search 等价。"""
        self.initialize()

        top_k = top_k if top_k is not None else settings.RAG_TOP_K
        similarity_threshold = similarity_threshold if similarity_threshold is not None else settings.RAG_SIMILARITY_THRESHOLD
        top_k = max(1, min(int(top_k), 50))
        similarity_threshold = max(0.0, min(float(similarity_threshold), 1.0))

        cache_key = QueryCache.make_key(query, top_k, similarity_threshold)
        cached = await self._query_cache.aget(cache_key)
        if cached:
            return {**cached, "from_cache": True}

        started = time.perf_counter()
        if self._vector_store:
            try:
                query_embedding = await asyncio.to_thread(self._encode_query_embedding, query)
                if query_embedding is None:
                    return self._embedding_failed_result(query, started)
                result = await asyncio.to_thread(
                    self._search_milvus, query, top_k, similarity_threshold, started, cache_key
                )
                await self._query_cache.aset(cache_key, result)
                return result
            except Exception as exc:
                logger.error(f"Milvus hybrid search failed (async), falling back to memory: {exc}")

        query_embedding = await asyncio.to_thread(self._encode_query_embedding, query)
        if query_embedding is None:
            return self._embedding_failed_result(query, started)

        vector_task = asyncio.to_thread(self._retrieve_vector, query_embedding, query, top_k, similarity_threshold)
        bm25_task = asyncio.to_thread(self._retrieve_bm25, query, top_k)
        vector_results, bm25_results = await asyncio.gather(vector_task, bm25_task)
        result = await asyncio.to_thread(
            self._compose_result, query, top_k, similarity_threshold,
            vector_results, bm25_results, started, cache_key,
        )
        # 写 Redis 共享缓存（_compose_result 已同步写入本地内存，双端保持一致），
        # 让多 worker 复用本次 embedding/检索结果。
        await self._query_cache.aset(cache_key, result)
        return result

    def _search_milvus(
        self,
        query: str,
        top_k: int,
        similarity_threshold: float,
        started: float,
        cache_key: str,
    ) -> Dict[str, Any]:
        """主链路：Milvus 2.5 原生混合检索（dense 向量 + FULLTEXT BM25，服务端 RRF 融合）。

        不再由应用进程内存 BM25 打分与合并；查询扩展词仍本地拼接后送入全文字段，
        语义与旧内存 BM25 等价但索引在服务端维护（多 worker/多副本共享一致）。
        """
        query_embedding = self._encode_query_embedding(query)
        if query_embedding is None:
            return self._embedding_failed_result(query, started)

        expanded = self._expand_bm25_query(query)
        try:
            merged = self._vector_store.hybrid_search(
                query_embedding=query_embedding,
                query_text=expanded,
                top_k=top_k,
            )
        except Exception:
            # FULLTEXT 索引缺失/未就绪时退化为纯向量检索，不阻断主链路。
            merged = self._vector_store.search(
                query_embedding=query_embedding,
                top_k=top_k * 4,
                score_threshold=similarity_threshold,
            )
        if not merged:
            # 服务端无结果（如集合为空）时回退内存向量检索（含本地持久化文档）。
            merged = self._memory_search(query_embedding, query, top_k, similarity_threshold)

        reranked = self._rerank(query, merged, top_k) if settings.RAG_USE_RERANKER else merged[:top_k]
        if not settings.RAG_USE_RERANKER:
            self._rerank_backend = "none"

        result = {
            "success": True,
            "query": query,
            "results": reranked,
            "total_candidates": len(merged),
            "search_strategy": "hybrid_milvus" if merged else "empty",
            "from_cache": False,
            "backend": self.backend,
            "reranker": self._rerank_backend,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason or None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        return result

    def _encode_query_embedding(self, query: str) -> Optional[List[float]]:
        """返回查询向量（走查询侧指令前缀）；embedding 不可用时返回 None。"""
        try:
            return self._embedding_service.encode_query(query)
        except RuntimeError as e:
            logger.error(f"Embedding 不可用，检索无法执行: {e}")
            return None

    def _embedding_failed_result(self, query: str, started: float) -> Dict[str, Any]:
        return {
            "success": False,
            "query": query,
            "results": [],
            "total_candidates": 0,
            "search_strategy": "empty",
            "from_cache": False,
            "backend": self.backend,
            "degraded": True,
            "degraded_reason": "embedding_unavailable",
            "error": "embedding_unavailable",
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    def _retrieve_vector(
        self, query_embedding: List[float], query: str, top_k: int, similarity_threshold: float
    ) -> List[Dict[str, Any]]:
        """向量路：Milvus 优先，失败/不可用回退内存向量检索。"""
        if self._vector_store:
            try:
                vector_results = self._vector_store.search(
                    query_embedding=query_embedding,
                    top_k=top_k * 4,
                    score_threshold=similarity_threshold,
                )
                if not vector_results:
                    vector_results = self._memory_search(query_embedding, query, top_k, similarity_threshold)
            except Exception as e:
                logger.error(f"Milvus search error: {e}")
                vector_results = self._memory_search(query_embedding, query, top_k, similarity_threshold)
        else:
            vector_results = self._memory_search(query_embedding, query, top_k, similarity_threshold)
        return vector_results

    def _retrieve_bm25(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        """BM25 路：查询扩展后关键词检索。"""
        return self._bm25_search(self._expand_bm25_query(query), top_k)

    def _compose_result(
        self, query: str, top_k: int, similarity_threshold: float,
        vector_results: List[Dict[str, Any]], bm25_results: List[Dict[str, Any]],
        started: float, cache_key: str,
    ) -> Dict[str, Any]:
        """RRF 融合 + 可选重排 + 组装结果并写缓存。"""
        merged = self._merge_results(vector_results, bm25_results, top_k)

        # RAG_USE_RERANKER 开启时走模型 reranker（外部→本地），未配置/失败
        # 回退启发式；关闭时直接使用 RRF 融合结果（仅按序截断）。
        reranked = self._rerank(query, merged, top_k) if settings.RAG_USE_RERANKER else merged
        if not settings.RAG_USE_RERANKER:
            self._rerank_backend = "none"

        strategy = "hybrid" if (vector_results and bm25_results) else (
            "vector" if vector_results else "memory"
        )
        if not vector_results and not bm25_results:
            strategy = "empty"

        result = {
            "success": True,
            "query": query,
            "results": reranked,
            "total_candidates": len(merged),
            "search_strategy": strategy,
            "from_cache": False,
            "backend": self.backend,
            "reranker": self._rerank_backend,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason or None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }

        self._query_cache.set(cache_key, result)
        return result

    @staticmethod
    def _expand_bm25_query(query: str) -> str:
        """为少量明确领域词补充同义词，扩大候选召回，不改变原始语义。"""
        query_lower = query.lower()
        expansions: list[str] = []
        if any(term in query_lower for term in ("赠品", "礼包", "买赠", "送什么")):
            expansions.extend(["优惠", "促销", "活动", "会员", "礼包"])
        if any(term in query_lower for term in ("便宜", "划算", "实惠", "性价比", "贵不贵", "多少钱")):
            expansions.extend(["优惠", "折扣", "满减", "促销", "活动", "会员"])
        if any(term in query_lower for term in ("下班", "几点", "营业时间", "上班时间", "工作时间")):
            expansions.extend(["客服", "工作时间", "服务时间", "客服时间", "FAQ"])
        # 退款/退换货类短查询扩展，帮助"能退吗""怎么退"命中售后政策文档
        if any(term in query_lower for term in ("能退", "可以退", "好退", "给退", "退吗", "怎么退", "如何退", "退款", "退货", "退换")):
            expansions.extend(["退换货", "无理由", "售后", "政策", "退款流程", "退货流程"])
        # 换货/换颜色类查询
        if any(term in query_lower for term in ("换货", "换一个", "换个", "换颜色")):
            expansions.extend(["退换货", "换货流程", "售后", "政策"])
        return " ".join([query, *expansions])

    def _bm25_search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        """内存 BM25 检索（仅在 Milvus 不可用时的降级链路使用）。

        Milvus 正常时全文检索由服务端 FULLTEXT 索引完成，本方法不再作为主链路。
        """
        corpus = []
        meta_chunks = []

        for doc in self._documents:
            if not doc.get("is_published", True):
                continue
            for chunk in doc["chunks"]:
                corpus.append(chunk["content"])
                meta_chunks.append({
                    "id": chunk["id"],
                    "title": doc["title"],
                    "category": doc.get("category", ""),
                    "keywords": doc.get("keywords", []),
                    "source": doc.get("source", ""),
                })

        if not corpus:
            return []

        if not self._bm25_built:
            self._bm25.build_index(corpus)
            self._bm25_built = True

        results = []
        for idx, meta in enumerate(meta_chunks):
            score = self._bm25.score(query, idx)
            if score > 0:
                results.append({
                    "id": meta["id"],
                    "title": meta.get("title", ""),
                    "content": corpus[idx] if idx < len(corpus) else "",
                    "category": meta.get("category", ""),
                    "keywords": meta.get("keywords", []),
                    "source": meta.get("source", ""),
                    "vector_score": 0.0,
                    "bm25_score": score,
                    "final_score": score,
                })

        results.sort(key=lambda x: x.get("bm25_score", 0), reverse=True)
        return results[:top_k * 4]

    def _merge_results(
        self,
        vector_results: List[Dict[str, Any]],
        bm25_results: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """使用 RRF（Reciprocal Rank Fusion）融合向量检索与 BM25 结果。

        RRF 只依赖两路检索的排名而非原始分数，避免向量分（余弦相似度）
        与 BM25 分（词频统计分）量纲不同导致的归一化失真，也无需手动调节
        权重。公式：score(d) = Σ 1 / (k + rank_i(d))，k 取经验常数 60。
        """
        if not bm25_results:
            return vector_results
        if not vector_results:
            return bm25_results

        k = 60  # RRF 经验常数，对最终排序不敏感
        merged: Dict[str, Dict[str, Any]] = {}

        # 向量检索路：按排名累计 RRF 分，同时保留原始 vector_score 便于排查
        for rank, doc in enumerate(vector_results):
            doc_id = doc.get("id", "")
            if doc_id not in merged:
                merged[doc_id] = doc.copy()
                merged[doc_id]["vector_score"] = doc.get("vector_score", 0.0)
                merged[doc_id]["bm25_score"] = 0.0
                merged[doc_id]["rrf_score"] = 0.0
            merged[doc_id]["rrf_score"] += 1.0 / (k + rank + 1)
            merged[doc_id]["vector_score"] = max(
                merged[doc_id].get("vector_score", 0.0),
                doc.get("vector_score", 0.0),
            )

        # BM25 检索路：按排名累计 RRF 分，同时保留原始 bm25_score 便于排查
        for rank, doc in enumerate(bm25_results):
            doc_id = doc.get("id", "")
            if doc_id not in merged:
                merged[doc_id] = doc.copy()
                merged[doc_id]["vector_score"] = 0.0
                merged[doc_id]["bm25_score"] = doc.get("bm25_score", 0.0)
                merged[doc_id]["rrf_score"] = 0.0
            merged[doc_id]["rrf_score"] += 1.0 / (k + rank + 1)
            merged[doc_id]["bm25_score"] = max(
                merged[doc_id].get("bm25_score", 0.0),
                doc.get("bm25_score", 0.0),
            )

        for doc in merged.values():
            doc["final_score"] = round(doc.pop("rrf_score"), 6)

        results = list(merged.values())
        results.sort(key=lambda x: x.get("final_score", 0), reverse=True)
        # 候选池保留 4 倍 top_k，给重排器留足提升空间：正确答案若只排在
        # 候选 11-20 位（对 top_k=5），此前在 2 倍截断时被直接丢弃导致漏检。
        return results[:top_k * 4]

    def _memory_search(
        self,
        query_embedding: List[float],
        query: str,
        top_k: int,
        threshold: float,
    ) -> List[Dict[str, Any]]:
        results = []
        for doc in self._documents:
            if not doc.get("is_published", True):
                continue
            for chunk in doc["chunks"]:
                if "_embedding" not in chunk:
                    continue

                similarity = self._cosine_similarity(query_embedding, chunk["_embedding"])
                if similarity >= threshold:
                    results.append({
                        "id": chunk["id"],
                        "title": doc["title"],
                        "content": chunk["content"],
                        "category": doc.get("category", ""),
                        "keywords": doc.get("keywords", []),
                        "source": doc.get("source", ""),
                        "vector_score": round(similarity, 4),
                        "final_score": round(similarity, 4),
                    })

        results.sort(key=lambda x: x["vector_score"], reverse=True)
        return results[:top_k * 4]

    def _rerank(
        self, query: str, candidates: List[Dict[str, Any]], top_k: int
    ) -> List[Dict[str, Any]]:
        """重排分发器：模型 reranker 优先（外部 API → 本地 bge-reranker-large），
        未配置/调用失败/超时回退启发式重排 _heuristic_rescore。同步执行
        （调用方均在 asyncio.to_thread 中），绝不阻塞事件循环。"""
        if candidates and settings.RAG_USE_RERANKER:
            if self._reranker is not None and self._reranker.is_available():
                rerank_input = candidates[: self.RERANK_MAX_CANDIDATES]
                out = self._reranker.rerank_sync(query, rerank_input, top_k)
                if out is not None:
                    self._rerank_backend = self._reranker.last_used_provider or "model"
                    return out
                self._rerank_backend = "heuristic"
                logger.warning("模型 reranker 失败/超时，回退启发式重排")
                return self._heuristic_rescore(query, candidates, top_k)
            self._rerank_backend = "heuristic"
            return self._heuristic_rescore(query, candidates, top_k)
        self._rerank_backend = "none"
        return candidates[:top_k]

    def _heuristic_rescore(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """RRF 融合后的启发式重排（非模型 reranker）。

        名实说明：本函数不调用 cross-encoder 模型，只做标题/正文/关键词/类目命中
        与领域词加权（domain_boosts）。若未来接入 gte-rerank 等模型 reranker，
        应作为独立函数置于本重排之前，并保持超时回退到本启发式路径。
        """
        if not candidates:
            return []

        query_lower = query.lower()
        query_tokens = BM25Scorer._tokenize(query)
        query_chars = set(re.findall(r'[\u4e00-\u9fff]|[a-zA-Z0-9]+', query_lower))
        scored = []
        seen_contents = set()

        for candidate in candidates:
            content_hash = candidate.get("content", "")[:100]
            if content_hash in seen_contents:
                continue
            seen_contents.add(content_hash)

            score = candidate.get("final_score", candidate.get("vector_score", 0))

            title = candidate.get("title", "").lower()
            content = candidate.get("content", "").lower()

            if query_lower in title:
                score += 0.3
            elif any(t in title for t in query_chars):
                score += 0.15

            if query_lower in content:
                score += 0.2
            elif any(t in content for t in query_chars):
                score += 0.1

            title_words = set(re.findall(r'[a-zA-Z0-9]+|[\u4e00-\u9fff]', title))
            content_words = set(re.findall(r'[a-zA-Z0-9]+|[\u4e00-\u9fff]', content))
            token_overlap = sum(1 for t in query_tokens if t in title_words or t in content_words)
            score += min(token_overlap * 0.04, 0.2)

            keywords = candidate.get("keywords", [])
            if isinstance(keywords, str):
                keywords = [k.strip() for k in keywords.split(",") if k.strip()]
            if isinstance(keywords, list):
                keyword_match = sum(
                    1 for kw in keywords
                    if kw.lower() in query_lower or query_lower in kw.lower()
                )
                score += min(keyword_match * 0.1, 0.3)

            cat = candidate.get("category", "")
            if cat:
                cat_lower = cat.lower()
                # 双向包含：category 出现在 query 中，或 query 关键词出现在 category 中
                if cat_lower in query_lower or any(
                    t in cat_lower for t in query_tokens if len(t) >= 2
                ):
                    score += 0.15

            # FAQ 问题与用户问题高度相似时优先返回（不依赖额外模型）。
            if str(candidate.get("category", "")).lower() == "faq" or "faq" in title:
                question_match = re.search(r"(?:\*{0,2}\s*)?(?:q|问)\s*[:：]\s*(.+?)(?:\*{0,2})?(?:\n|$)", content, re.IGNORECASE)
                if question_match:
                    faq_question = question_match.group(1).strip(" *：:")
                    normalized_query = re.sub(r"[\s\W_]+", "", query_lower)
                    normalized_question = re.sub(r"[\s\W_]+", "", faq_question.lower())
                    if normalized_question and (
                        normalized_query == normalized_question
                        or SequenceMatcher(None, normalized_query, normalized_question).ratio() >= 0.85
                    ):
                        score += 0.5

            # 领域加权：退款/退换货问题偏向售后政策，优惠问题偏向会员与满减文档。
            if any(term in query_lower for term in ("退款", "退货", "退换", "退费", "售后")):
                if any(term in f"{title} {content} {cat}" for term in ("退换货", "质保", "售后")):
                    score += 0.1
            if any(term in query_lower for term in ("优惠", "满减", "折扣", "优惠券", "赠品", "礼包")):
                if any(term in f"{title} {content} {cat}" for term in ("会员", "满减", "优惠券", "促销", "活动")):
                    score += 0.1
            # 通用咨询类查询（营业时间/地址/电话等）偏向 FAQ 文档
            if any(term in query_lower for term in ("下班", "几点", "营业时间", "地址", "在哪", "电话", "联系方式", "工作时间", "上班")):
                if str(cat).lower() == "faq" or "faq" in title:
                    score += 0.2

            # 领域消歧加权：订单/下单/售后/支付等高频词跨文档互相抢占时，
            # 按查询主题给目标文档统一补权；命中多类时多类同时补权，
            # 只在两文档分差小于补权差时翻转，不破坏既有正确排序。
            domain_boosts = [
                # (查询触发词, 目标文档特征词, 补权)
                (("下单", "购物车", "提交订单"), ("购物下单", "购物车", "结算"), 0.15),
                (("订单状态", "取消", "历史订单", "已付款", "待付款", "拦截", "签收", "派送中", "物流轨迹", "订单号"), ("订单查询", "物流追踪", "订单状态"), 0.2),
                (("售后", "工单", "审核"), ("售后", "工单"), 0.15),
                (("扣款", "货到付款", "境外卡", "信用卡", "支付密码", "微信支付", "未扣款"), ("支付", "扣款", "付款"), 0.2),
                (("投诉", "举报", "意见", "反馈"), ("投诉", "意见", "举报"), 0.15),
                (("维修", "保修", "质保", "过保"), ("质保", "维修", "保修"), 0.2),
                (("新客", "新人", "首单", "新用户"), ("新用户", "首单"), 0.2),
                (("隐私", "数据", "共享", "公开", "收集", "删除", "记录"), ("隐私", "数据"), 0.2),
                (("账号", "密码", "登录", "验证码", "掉线", "两步验证"), ("账号", "密码", "验证码"), 0.2),
                (("官方", "官网", "联系", "App", "商店", "几点", "工作时间", "常见问题", "最常问"), ("FAQ", "faq"), 0.2),
                (("企业", "采购", "对公", "赊账", "合同"), ("企业采购", "批量定制"), 0.25),
                (("优惠券", "满减", "折扣", "领取"), ("满减", "优惠券", "促销"), 0.15),
                (("会员", "积分", "VIP"), ("会员", "VIP"), 0.15),
                (("分期", "免息", "征信", "还款"), ("分期", "信贷"), 0.15),
                (("礼品卡", "储值卡", "余额"), ("礼品卡", "储值卡"), 0.15),
                (("物流", "配送", "运费", "时效", "偏远", "加急"), ("物流", "配送"), 0.15),
                (("以旧换新", "回收", "旧机"), ("以旧换新", "回收"), 0.15),
                (("兼容", "适配", "安卓", "苹果", "iOS", "平板", "品牌", "系统"), ("兼容", "适配"), 0.15),
                (("发票", "税号", "开票", "抬头"), ("发票", "税号"), 0.15),
                (("退款", "退货", "无理由", "退换"), ("退换货", "无理由"), 0.15),
                (("手表",), ("智能手表",), 0.15),
                (("耳机",), ("蓝牙耳机",), 0.15),
                (("键盘",), ("机械键盘",), 0.15),
                (("显示器", "屏幕"), ("显示器",), 0.15),
            ]
            for triggers, markers, boost in domain_boosts:
                if any(t in query_lower for t in triggers):
                    if any(m in f"{title} {content} {cat}" for m in markers):
                        score += boost

            if len(content) > 50:
                score += 0.02

            candidate["final_score"] = round(score, 4)
            scored.append(candidate)

        scored.sort(key=lambda x: x["final_score"], reverse=True)
        return scored[:top_k]

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        min_len = min(len(vec1), len(vec2))
        vec1 = vec1[:min_len]
        vec2 = vec2[:min_len]

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = sum(a * a for a in vec1) ** 0.5
        magnitude2 = sum(b * b for b in vec2) ** 0.5

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        return dot_product / (magnitude1 * magnitude2)