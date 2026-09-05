"""面向知识库检索的保守问题改写。"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional


@dataclass(frozen=True)
class QueryRewriteResult:
    """一次改写的结果及可观测信息。"""

    original_query: str
    rewritten_query: str
    applied: bool
    reason: str = ""
    confidence: float = 0.0


class ConservativeQueryRewriter:
    """仅在当前问题明显依赖上文时，把上文拼入检索查询。"""

    _REFERENCE_TERMS = (
        "这个", "那个", "这款", "那款", "它", "该产品", "该商品", "上述", "前面"
    )
    _FOLLOW_UP_TERMS = (
        "然后", "那", "另外", "还", "仍然", "还是", "多久", "可以吗", "能吗", "呢", "吗"
    )
    _ORDER_ID_PATTERN = re.compile(r"\bORD\d{6,}\b", re.IGNORECASE)

    def rewrite(
        self,
        query: str,
        messages: Optional[Iterable[Dict[str, Any]]] = None,
        collected_info: Optional[Dict[str, Any]] = None,
    ) -> QueryRewriteResult:
        original = " ".join(str(query or "").split())
        if not original:
            return QueryRewriteResult(original, original, False, "empty_query", 1.0)

        previous = self._previous_user_message(original, messages or [])
        if not previous:
            return QueryRewriteResult(original, original, False, "no_previous_user_query", 1.0)

        compact = original.replace(" ", "")
        has_reference = any(term in compact for term in self._REFERENCE_TERMS)
        has_follow_up = any(
            compact.startswith(term) or compact.endswith(term)
            for term in self._FOLLOW_UP_TERMS
        )
        is_short = len(compact) <= 20
        if not (has_reference or (is_short and has_follow_up)):
            return QueryRewriteResult(original, original, False, "self_contained_query", 0.9)

        info = collected_info or {}
        order_id = str(info.get("order_id") or "").strip()
        if not order_id:
            order_match = self._ORDER_ID_PATTERN.search(previous)
            order_id = order_match.group(0).upper() if order_match else ""

        context = previous[:160]
        rewritten = f"{context}；用户追问：{original}"
        if order_id and order_id not in rewritten.upper():
            rewritten = f"{rewritten}（订单号：{order_id}）"
        rewritten = rewritten[:300]

        reason = "reference_with_context" if has_reference else "short_follow_up"
        confidence = 0.92 if has_reference else 0.78
        return QueryRewriteResult(original, rewritten, True, reason, confidence)

    @staticmethod
    def _previous_user_message(
        current_query: str, messages: Iterable[Dict[str, Any]]
    ) -> Optional[str]:
        skipped_current = False
        for message in reversed(list(messages)):
            if message.get("role") != "user":
                continue
            content = " ".join(str(message.get("content") or "").split())
            if not content:
                continue
            if not skipped_current and content == current_query:
                skipped_current = True
                continue
            return content
        return None
