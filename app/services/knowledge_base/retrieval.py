"""兼容纯融合/启发式重排助手，生产检索统一走versioned_search.py。

保留旧纯函数测试与导入契约，真实双路/分数类型/版本过滤不使用旧执行链。
"""
import logging
import re
import time
from difflib import SequenceMatcher
from typing import Any, Dict, List

from app.config.config import settings

from .bm25 import BM25Scorer

logger = logging.getLogger(__name__)


class RetrievalMixin:
    """混合检索链路：Milvus 原生 hybrid、内存双路 RRF 融合、模型/启发式重排。"""

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
            content_hash = candidate.get("id") or __import__("hashlib").sha256(candidate.get("content", "").encode()).hexdigest()
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
        if len(vec1)!=len(vec2):
            raise ValueError("embedding_dimension_mismatch")

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = sum(a * a for a in vec1) ** 0.5
        magnitude2 = sum(b * b for b in vec2) ** 0.5

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        return dot_product / (magnitude1 * magnitude2)