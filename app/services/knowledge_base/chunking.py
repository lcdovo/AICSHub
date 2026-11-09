"""结构优先的原文分块；所有片段保持精确、连续的原文位置。"""
from __future__ import annotations

import hashlib
import re
from bisect import bisect_right
from typing import Any

CHUNK_POLICY_VERSION = "structured-source-v2"
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_QUESTION = re.compile(r"^\s*\*{0,2}(?:Q\d*|问)\s*[:：].+", re.I)
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*\|")


def _units(content: str) -> list[tuple[int, int, dict[str, Any]]]:
    """标题/问答是硬边界；围栏代码内的标题和问号不参与解析。"""
    starts: list[tuple[int, dict[str, Any]]] = []
    headings: list[tuple[int, str]] = []
    question = ""
    table_header = ""
    offset = 0
    fence: str | None = None
    previous_line = ""
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        in_fence = fence is not None
        if marker:
            symbol = marker.group(1)
            if fence is None:
                fence = symbol
            elif symbol[0] == fence[0] and len(symbol) >= len(fence):
                fence = None
        heading = _HEADING.match(line.rstrip("\r\n")) if not in_fence and not marker else None
        is_question = bool(_QUESTION.match(line)) if not in_fence and not marker else False
        boundary = offset == 0
        if heading:
            level = len(heading.group(1))
            headings = [(n, text) for n, text in headings if n < level]
            headings.append((level, heading.group(2)))
            question, table_header = "", ""
            boundary = True
        elif is_question:
            question, table_header = stripped, ""
            boundary = True
        if not in_fence and _TABLE_RULE.match(line) and "|" in previous_line:
            table_header = previous_line.rstrip("\r\n") + "\n" + line.rstrip("\r\n")
        if boundary:
            starts.append((offset, {
                "heading": " / ".join(text for _, text in headings),
                "heading_path": [text for _, text in headings],
                "faq_question": question,
                "table_header": table_header,
            }))
        offset += len(line)
        previous_line = line
    if not starts:
        return []
    units = [(start, starts[i + 1][0] if i + 1 < len(starts) else len(content), context)
             for i, (start, context) in enumerate(starts)]
    merged = []
    pending_start = None
    for i, (start, end, context) in enumerate(units):
        lines = [line for line in content[start:end].splitlines() if line.strip()]
        # 只有标题的父章节并入首个子节/问答，避免生成无正文的标题片段。
        if (not lines or all(_HEADING.match(line) for line in lines)) and i + 1 < len(units):
            pending_start = start if pending_start is None else pending_start
            continue
        merged.append((pending_start if pending_start is not None else start, end, context))
        pending_start = None
    return merged


def _ranges(content: str, start: int, end: int, size: int, overlap: int,
            pattern: str) -> list[tuple[int, int]]:
    spans = []
    while start < end:
        stop = min(end, start + size)
        if stop < end and pattern != "fixed":
            # 优先段落、整行（列表/表格），其次句末，最后才硬切长句。
            floor = start + size // 2
            blocks = (("\n\n", "\r\n\r\n"), ("\n",), ("。", "！", "？", "；", ". ", "! ", "? "))
            if pattern == "sentence":
                blocks = (blocks[2], blocks[0], blocks[1])
            for separators in blocks:
                positions = [(content.rfind(mark, floor, stop), len(mark)) for mark in separators]
                position, width = max(positions)
                if position >= floor:
                    stop = position + width
                    break
        spans.append((start, stop))
        if stop == end:
            break
        start = max(start + 1, stop - overlap)
    return spans


def source_chunks(content: str, size: int = 400, overlap: int = 50,
                  split_pattern: str = "heading") -> list[dict[str, Any]]:
    """长度为原文字符硬上限；重叠只发生在同一结构单元内。"""
    if size < 64 or overlap < 0 or overlap >= size:
        raise ValueError("invalid_chunk_policy")
    if split_pattern not in {"heading", "title", "按标题", "sentence", "paragraph", "fixed"}:
        raise ValueError("invalid_chunk_pattern")
    chunks = []
    for unit_start, unit_end, unit_context in _units(content):
        # 同一单元内定位表头，不能跨章节沿用不相关表格或FAQ。
        table_events = []
        previous = ""
        cursor = unit_start
        fence = False
        for line in content[unit_start:unit_end].splitlines(keepends=True):
            if re.match(r"^\s*(`{3,}|~{3,})", line):
                fence = not fence
            if not fence and _TABLE_RULE.match(line) and "|" in previous:
                table_events.append((cursor, previous.rstrip("\r\n") + "\n" + line.rstrip("\r\n")))
            elif not fence and line.strip() and "|" not in line:
                table_events.append((cursor, ""))
            previous = line
            cursor += len(line)
        table_positions = [position for position, _ in table_events]
        for start, end in _ranges(content, unit_start, unit_end, size, overlap, split_pattern):
            context = dict(unit_context)
            header_index = bisect_right(table_positions, start) - 1
            context["table_header"] = table_events[header_index][1] if header_index >= 0 else ""
            context["unit_id"] = f"{unit_start}:{unit_end}"
            body = content[start:end]
            chunks.append({
                "content": body, "source_context": context,
                "start_offset": start, "end_offset": end,
                "section": context["heading"][:200],
                "content_hash": hashlib.sha256(body.encode()).hexdigest(),
                "chunk_policy": CHUNK_POLICY_VERSION,
            })
    return chunks
