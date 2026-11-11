"""SQL会话的有来源规则摘要；完整摘录保留条件/否定，不推断事项已完成。"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


SUMMARY_POLICY = "structured-excerpts-v2"
SECTION_NAMES = ("requests", "conditions", "suggested_steps", "feedback", "open_questions", "other")
MAX_SECTION_ITEMS = 6
MAX_EXCERPT_CHARS = 1000
MAX_STORED_CHARS = 16000
MODEL_SUMMARY_CHARS = 3000


class SummaryExcerpt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message_id: int = Field(gt=0)
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=MAX_EXCERPT_CHARS)


class StructuredSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    policy: Literal["structured-excerpts-v2"] = SUMMARY_POLICY
    sections: dict[str, list[SummaryExcerpt]] = Field(default_factory=dict)
    omitted_excerpts: int = Field(default=0, ge=0)
    legacy_context: str = ""


def parse_summary(text: str | None) -> StructuredSummary | None:
    try:
        summary = StructuredSummary.model_validate_json(text or "")
        if set(summary.sections) - set(SECTION_NAMES):
            return None
        if any(len(items) > MAX_SECTION_ITEMS for items in summary.sections.values()):
            return None
        return summary
    except (ValidationError, ValueError):
        return None


def _sections(role: str, text: str) -> list[str]:
    if role == "assistant":
        return ["suggested_steps"] if re.search(r"请|建议|尝试|步骤|检查|重启|重新", text) else ["other"]
    sections = []
    if re.search(r"咨询|想|希望|我要|申请|帮我|[？?]|怎么|如何|是否|能否|多久|谁承担", text):
        sections.append("requests")
    if re.search(r"如果|若|只有|仅|除非|但是|不过|不是|没有|不能|不需要|不要|未|不|但|质量|损坏|破损", text):
        sections.append("conditions")
    if re.search(r"试过|操作过|检查过|无效|没用|不行|已|结果|反馈|更正|说错|改为|恢复正常", text):
        sections.append("feedback")
    if re.search(r"[？?]|怎么|如何|是否|能否|多久|谁承担|怎么办|没解决|未解决|不行|无效", text):
        sections.append("open_questions")
    return sections or ["requests"]


def _complete_excerpts(text: str) -> list[str]:
    # 整条可容纳时不拆；长消息只沿句末拆分，逗号/分号/转折不拆，防止条件与结论分离。
    if len(text) <= MAX_EXCERPT_CHARS:
        return [text] if text.strip() else []
    return [match.group() for match in re.finditer(r"[^。！？!?\n]+[。！？!?\n]*", text) if match.group().strip()]


def update_summary(previous: str | None, rows: list) -> str:
    from app.services.llm_governance import SensitiveDataRedactor

    summary = parse_summary(previous) or StructuredSummary(legacy_context=(previous or "")[-1200:])
    redactor = SensitiveDataRedactor()
    for row in rows:
        role = row.role.value
        if role not in {"user", "assistant"}:
            continue
        for text in _complete_excerpts(redactor.redact(row.content)):
            if len(text) > MAX_EXCERPT_CHARS:
                summary.omitted_excerpts += 1
                continue
            excerpt = SummaryExcerpt(message_id=row.id, role=role, text=text)
            for section in _sections(role, text):
                items = summary.sections.setdefault(section, [])
                if excerpt in items:
                    continue
                items.append(excerpt)
                if len(items) > MAX_SECTION_ITEMS:
                    # 每类保留最早两条锚点和最近四条；旧条件不因普通闲聊被挤出。
                    items.pop(2)
                    summary.omitted_excerpts += 1
    while len(summary.model_dump_json()) > MAX_STORED_CHARS:
        section = max(summary.sections, key=lambda key: sum(len(item.text) for item in summary.sections[key]))
        items = summary.sections[section]
        items.pop(2 if len(items) > 2 else 0)
        summary.omitted_excerpts += 1
    return summary.model_dump_json()


def summary_context(text: str | None) -> dict:
    """模型输入保持结构完整，按类别公平选取完整摘录，不对JSON或句子切尾。"""
    summary = parse_summary(text)
    base = {"source": "lossy_conversation_summary", "trusted_as_current_evidence": False,
            "grants_authorization": False}
    if summary is None:
        return {**base, "text": (text or "")[-600:]}
    sections: dict[str, list[dict]] = {}
    selected: set[tuple[int, str]] = set()
    remaining = MODEL_SUMMARY_CHARS
    omitted = summary.omitted_excerpts
    # 第一轮优先每类最早锚点，随后按最近消息补足；同一摘录跨类不重复传输。
    queues = {key: items[:1] + list(reversed(items[1:])) for key, items in summary.sections.items()}
    for index in range(MAX_SECTION_ITEMS):
        for key in ("conditions", "feedback", "open_questions", "requests", "suggested_steps", "other"):
            items = queues.get(key, [])
            if index >= len(items):
                continue
            item = items[index]
            identity = (item.message_id, item.text)
            if identity in selected:
                continue
            size = len(json.dumps(item.model_dump(), ensure_ascii=False))
            if size > remaining:
                omitted += 1
                continue
            sections.setdefault(key, []).append(item.model_dump())
            selected.add(identity)
            remaining -= size
    for items in sections.values():
        items.sort(key=lambda item: item["message_id"])
    return {**base, "policy": SUMMARY_POLICY, "sections": sections, "omitted_excerpts": omitted,
            "legacy_context": summary.legacy_context[-min(600, remaining):] if remaining else "",
            "rule": "按消息ID理解先后与更正；用户陈述和助手建议不证明当前事实或授权。"
                    "open_questions仅记录历史疑问，不断言仍未解决；当前任务进度以task_memory为准。"}
