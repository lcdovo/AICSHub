"""文档接入层：分块器与文档入库 Mixin（上传/分块/embedding/持久化）。

自 knowledge_base.py 拆分迁移（原 DocumentChunker / add_document /
_save_to_database / update_document），逻辑保持原样。
"""
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config.config import settings

logger = logging.getLogger(__name__)


class DocumentChunker:
    """文档分块器 - 将长文档切分成适合向量化的小块"""

    def __init__(
        self,
        chunk_size: int = None,
        chunk_overlap: int = None,
        split_pattern: str = None,
    ):
        self.chunk_size = chunk_size or settings.CHUNK_SIZE
        self.chunk_overlap = chunk_overlap or settings.CHUNK_OVERLAP
        self.split_pattern = split_pattern or settings.CHUNK_SPLIT_PATTERN

    def chunk_document(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if not content or not content.strip():
            return []

        is_faq = self._is_faq(metadata)
        if is_faq:
            chunks = self._split_faq(content, metadata)
        elif self.split_pattern in {"heading", "title", "按标题"}:
            chunks = self._split_by_heading(content, metadata)
        elif self.split_pattern == "sentence":
            chunks = self._split_by_sentence(content)
        elif self.split_pattern == "paragraph":
            chunks = self._split_by_paragraph(content)
        else:
            chunks = self._split_fixed(content)

        result = []
        for i, chunk_text in enumerate(chunks):
            chunk_text = chunk_text.strip()
            if not chunk_text:
                continue

            chunk = {
                "id": f"chunk_{uuid.uuid4().hex[:12]}",
                "content": chunk_text,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "char_count": len(chunk_text),
                "created_at": datetime.now().isoformat(),
            }
            if metadata:
                chunk["metadata"] = metadata
            result.append(chunk)

        logger.info(f"文档分块完成: {len(content)} 字符 -> {len(result)} 个分块")
        return result

    @staticmethod
    def _is_faq(metadata: Optional[Dict[str, Any]]) -> bool:
        metadata = metadata or {}
        title = str(metadata.get("title", "")).strip().lower()
        category = str(metadata.get("category", "")).strip().lower()
        source = str(metadata.get("source", "")).strip().lower()
        return title in {"faq", "faq.md"} or category == "faq" or source.endswith("faq.md")

    @staticmethod
    def _heading_prefix(metadata: Optional[Dict[str, Any]], heading: str, subsection: str = "") -> str:
        metadata = metadata or {}
        title = str(metadata.get("title") or metadata.get("source") or "未命名文档")
        title = title.split("/")[-1].split("\\")[-1]
        if title.lower().endswith(".md"):
            title = title[:-3].rstrip(".")
        chapter = heading.strip() or "文档概览"
        if subsection and subsection.strip() and subsection.strip() != chapter:
            chapter = f"{chapter} / {subsection.strip()}"
        return f"文档：{title}\n章节：{chapter}\n"

    def _split_by_heading(self, text: str, metadata: Optional[Dict[str, Any]]) -> List[str]:
        """按 Markdown 二级标题切分；超过 800 字的章节才按句切分。"""
        matches = list(re.finditer(r"(?m)^##(?!#)\s+(.+?)\s*$", text))
        sections: List[Any] = []
        if not matches:
            body = text.strip()
            parts = self._split_by_sentence(body) if len(body) > 800 else [body]
            # 无二级标题的旧格式保持兼容；规范 Markdown 文档均会走标题分块路径。
            return [part for part in parts if part.strip()]
        # 顶层 # 文档标题仅作为来源元数据，不单独占用一个分块。
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            sections.append((match.group(1).strip(), text[match.start():end].strip()))
        chunks: List[str] = []
        for heading, body in sections:
            parts = self._split_by_sentence(body) if len(body) > 800 else [body]
            prefix = self._heading_prefix(metadata, heading)
            chunks.extend(prefix + part.strip() for part in parts if part.strip())
        return chunks

    def _split_faq(self, text: str, metadata: Optional[Dict[str, Any]]) -> List[str]:
        """FAQ 按单个问答对切分，并保留所属章节。"""
        lines = text.splitlines()
        question_re = re.compile(r"^\s*\*{0,2}\s*(?:Q|问)\s*[:：]\s*(.+?)\s*\*{0,2}\s*$", re.IGNORECASE)
        blocks: List[Any] = []
        current: Optional[List[str]] = None
        current_heading, current_subsection = "文档概览", ""
        for line in lines:
            h2 = re.match(r"^##(?!#)\s+(.+?)\s*$", line)
            h3 = re.match(r"^###\s+(.+?)\s*$", line)
            if h2:
                if current:
                    blocks.append((current_heading, current_subsection, "\n".join(current).strip()))
                    current = None
                current_heading, current_subsection = h2.group(1).strip(), ""
            elif h3:
                if current:
                    blocks.append((current_heading, current_subsection, "\n".join(current).strip()))
                    current = None
                current_subsection = h3.group(1).strip()
            if question_re.match(line):
                if current:
                    blocks.append((current_heading, current_subsection, "\n".join(current).strip()))
                current = [line.strip()]
            elif current is not None:
                current.append(line.rstrip())
        if current:
            blocks.append((current_heading, current_subsection, "\n".join(current).strip()))
        if not blocks:
            return self._split_by_heading(text, metadata)
        return [self._heading_prefix(metadata, heading, subsection) + body
                for heading, subsection, body in blocks if body]

    def _split_by_sentence(self, text: str) -> List[str]:
        sentences = re.split(r'(?<=[。！？.!?；;])', text)
        sentences = [s.strip() for s in sentences if s.strip()]

        chunks = []
        current = ""
        for sentence in sentences:
            if len(current) + len(sentence) <= self.chunk_size:
                current += sentence
            else:
                if current:
                    chunks.append(current)
                if len(sentence) > self.chunk_size:
                    for i in range(0, len(sentence), self.chunk_size):
                        chunk = sentence[i:i + self.chunk_size]
                        if chunk:
                            chunks.append(chunk)
                    current = ""
                else:
                    current = sentence

        if current:
            chunks.append(current)

        return chunks

    def _split_by_paragraph(self, text: str) -> List[str]:
        paragraphs = re.split(r'\n\s*\n', text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        chunks = []
        for para in paragraphs:
            if len(para) <= self.chunk_size:
                chunks.append(para)
            else:
                sentences = re.split(r'(?<=[。！？.!?；;])', para)
                current = ""
                for sentence in sentences:
                    if len(current) + len(sentence) <= self.chunk_size:
                        current += sentence
                    else:
                        if current:
                            chunks.append(current)
                        current = sentence
                if current:
                    chunks.append(current)

        return chunks

    def _split_fixed(self, text: str) -> List[str]:
        chunks = []
        step = self.chunk_size - self.chunk_overlap
        if step <= 0:
            step = self.chunk_size

        for i in range(0, len(text), step):
            chunk = text[i:i + self.chunk_size]
            if chunk.strip():
                chunks.append(chunk)

        return chunks


from .lifecycle import LifecycleMixin


class IngestionMixin(LifecycleMixin):
    """旧导入名兼容；文档写入统一SQL版本仓储。"""
