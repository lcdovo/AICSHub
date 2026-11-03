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
    simple: bool = False  # 规则拼接已足够（简单指代），无需 LLM 语义补全


class ConservativeQueryRewriter:
    """仅在当前问题明显依赖上文时，把上文拼入检索查询。"""

    _REFERENCE_TERMS = (
        "这个", "那个", "这款", "那款", "它", "该产品", "该商品", "上述", "前面",
        "这些", "那些",
    )
    # 追问连接词：出现在句首且句子较短（≤14 字）时视为承接上一轮话题。
    _HEAD_FOLLOW_TERMS = ("那", "然后", "接着", "另外", "还", "而且")
    # 裸疑问短句（≤6 字）含以下疑问词/语气词时视为依赖上文的对象省略。
    _SHORT_QUESTION_HINTS = (
        "呢", "吗", "多久", "怎样", "怎么", "多少", "哪个", "哪些", "谁", "如何", "咋"
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
            return QueryRewriteResult(original, original, False, "empty_query", 1.0, True)

        previous = self._previous_user_message(original, messages or [])
        if not previous:
            return QueryRewriteResult(original, original, False, "no_previous_user_query", 1.0, False)

        # 先剥掉标点再匹配：避免"价格呢？""大概多久？"因句尾标点导致 endswith 失配。
        compact = re.sub(r"[？?!！。，,、～~·:：]+", "", original.replace(" ", ""))
        has_reference = any(term in compact for term in self._REFERENCE_TERMS)
        # 句首追问连接词（"那""还""然后"）且整体不长 → 承接上文
        is_head_follow = (
            len(compact) <= 14
            and any(compact.startswith(term) for term in self._HEAD_FOLLOW_TERMS)
        )
        # 裸疑问短句（如"价格呢""运费谁出""哪个更好用"）→ 主题词省略，依赖上文
        is_short_question = (
            len(compact) <= 6
            and any(hint in compact for hint in self._SHORT_QUESTION_HINTS)
        )
        if not (has_reference or is_head_follow or is_short_question):
            return QueryRewriteResult(original, original, False, "self_contained_query", 0.9, True)

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
        # 简单指代判定：追问短（≤10 字）+ 上文短（≤80 字）+ 至多一个指代词，
        # 此时规则拼接足够、质量不输 LLM 补全，可零延迟短路；否则交由 LLM
        # 从更长/更复杂的上下文中提取主题做语义补全。
        simple = (
            len(compact) <= 10
            and len(previous) <= 80
            and sum(1 for term in self._REFERENCE_TERMS if term in compact) <= 1
        )
        return QueryRewriteResult(original, rewritten, True, reason, confidence, simple)

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
