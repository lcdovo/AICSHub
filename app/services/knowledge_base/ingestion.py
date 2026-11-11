"""兼容导入入口，与版本化索引Job共用结构分块。"""
from app.utils.utc_time import utc_now
from datetime import datetime
from typing import Any
import uuid

from app.config.config import settings
from .chunking import source_chunks
from .retrieval_text import retrieval_text


class DocumentChunker:
    def __init__(self, chunk_size: int | None = None, chunk_overlap: int | None = None,
                 split_pattern: str | None = None) -> None:
        self.chunk_size = settings.CHUNK_SIZE if chunk_size is None else chunk_size
        self.chunk_overlap = settings.CHUNK_OVERLAP if chunk_overlap is None else chunk_overlap
        self.split_pattern = settings.CHUNK_SPLIT_PATTERN if split_pattern is None else split_pattern

    def chunk_document(self, content: str, metadata: dict[str, Any] | None = None) -> list[dict]:
        if not content or not content.strip():
            return []
        chunks = source_chunks(content, self.chunk_size, self.chunk_overlap, self.split_pattern)
        for index, chunk in enumerate(chunks):
            chunk.update(id=f"chunk_{uuid.uuid4().hex[:12]}", chunk_index=index,
                         total_chunks=len(chunks), char_count=len(chunk["content"]),
                         created_at=utc_now().isoformat())
            if metadata:
                chunk["metadata"] = dict(metadata)
            chunk["retrieval_text"] = retrieval_text({**(metadata or {}), **chunk})
        return chunks
