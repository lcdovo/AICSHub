"""召回、文档向量与重排共用的有界上下文；原文和偏移保持不变。"""
from __future__ import annotations

TEXT_POLICY_VERSION = "context-document-v2"
MAX_CONTEXT_CHARS = 1000


def retrieval_text(item: dict) -> str:
    body = str(item.get("content") or "")
    context = item.get("source_context") or {}
    fields = (
        ("文档", item.get("title")),
        ("章节", context.get("heading") or item.get("section")),
        ("问题", context.get("faq_question")),
        ("表头", context.get("table_header")),
        ("关键词", "、".join(str(v) for v in (item.get("keywords") or [])[:12])),
    )
    prefix = []
    remaining = MAX_CONTEXT_CHARS
    seen = set()
    for label, value in fields:
        text = str(value or "").strip()
        if not text or text in body or text in seen or remaining <= len(label) + 2:
            continue
        line = f"{label}：{text}"[:remaining - 1]
        prefix.append(line)
        remaining -= len(line) + 1
        seen.add(text)
    return "\n".join([*prefix, body])
