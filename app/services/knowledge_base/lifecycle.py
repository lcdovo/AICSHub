"""稳定文档生命周期与有界、可追溯原文分块。"""

from __future__ import annotations
import asyncio
import hashlib
import json
import re
from urllib.parse import urlsplit
from app.config.config import settings
from app.repositories.knowledge_repository import KnowledgeRepository


def embedding_identity(service) -> tuple[str, dict]:
    backend = getattr(service, "_embedding", None)
    provider = urlsplit(getattr(backend, "api_base", settings.EMBEDDING_API_BASE))
    metadata = {
        "provider": (
            provider.scheme
            + "://"
            + (provider.hostname or "")
            + (":" + str(provider.port) if provider.port else "")
            + provider.path
        ),
        "model": getattr(
            backend, "model", getattr(service, "backend_name", "unconfigured")
        ),
        "revision": settings.EMBEDDING_REVISION,
        "configured_model": settings.EMBEDDING_MODEL,
        "configured_dim": settings.EMBEDDING_DIM,
        "dim": getattr(service, "_dim", settings.EMBEDDING_DIM),
        "query_instruction": settings.EMBEDDING_QUERY_INSTRUCTION,
        "normalization": "cosine-v1",
    }
    return hashlib.sha256(
        json.dumps(metadata, sort_keys=True).encode()
    ).hexdigest(), metadata


def source_chunks(content: str, size: int = 400, overlap: int = 50) -> list[dict]:
    if size < 64 or overlap < 0 or overlap >= size:
        raise ValueError("invalid_chunk_policy")
    chunks = []
    start = 0
    section = ""
    while start < len(content):
        end = min(len(content), start + size)
        # 在上限内尽量选择完整行/句；原文范围完整覆盖，长句硬边界有明确重叠。
        if end < len(content):
            boundaries = [
                content.rfind(mark, start + size // 2, end)
                for mark in ("\n", "。", "；")
            ]
            if max(boundaries) >= 0:
                end = max(boundaries) + 1
        body = content[start:end]
        headings = list(re.finditer(r"(?m)^#{1,6}\s+(.+)$", content[:end]))
        if headings:
            section = headings[-1].group(1)[:200]
        before = content[:start]
        questions = list(re.finditer(r"(?m)^\s*(?:Q\d*|问)[:：].+$", before))
        table_headers = []
        lines = before.splitlines()
        for row in range(len(lines) - 1, 0, -1):
            if re.match(r"^\s*\|?\s*:?-{3,}", lines[row]):
                table_headers = lines[row - 1 : row + 1]
                break
        context = {
            "heading": section,
            "faq_question": questions[-1].group(0) if questions else "",
            "table_header": "\n".join(table_headers),
        }
        chunks.append(
            {
                "content": body,
                "source_context": context,
                "start_offset": start,
                "end_offset": end,
                "section": section,
                "content_hash": hashlib.sha256(body.encode()).hexdigest(),
                "chunk_policy": "bounded-source-v1",
            }
        )
        if end == len(content):
            break
        start = max(start + 1, end - overlap)
    return chunks


class LifecycleMixin:
    def initialize(self) -> None:
        if self._initialized:
            return
        from app.services.embedding_service import get_embedding_service

        self._embedding_service = self._embedding_service or get_embedding_service(
            dim=settings.EMBEDDING_DIM
        )
        self._repository = self._repository or KnowledgeRepository()
        if settings.USE_MILVUS and self._vector_store is None:
            from app.services.knowledge_base.vector_index import VersionedVectorIndex

            try:
                self._vector_store = VersionedVectorIndex(
                    settings.MILVUS_HOST, settings.MILVUS_PORT
                )
            except Exception:
                self.degraded = True
                self.degraded_reason = "milvus_unavailable"
        self.backend = "milvus" if self._vector_store else "sql"
        self._initialized = True

    def add_document(
        self,
        title,
        content,
        category="",
        keywords=None,
        source="",
        is_published=True,
        **kwargs,
    ):
        self.initialize()
        if not content.strip():
            raise ValueError("document_empty")
        return self._repository.save(
            title=title,
            content=content,
            category=category,
            keywords=keywords,
            source=source,
            publish=is_published,
            **kwargs,
        )

    def update_document(self, document_id, expected_version=None, **kwargs):
        self.initialize()
        old = self._repository.detail(document_id)
        values = {
            k: old[k] for k in ("title", "content", "category", "keywords", "source")
        }
        values.update(
            {k: v for k, v in kwargs.items() if k in values and v is not None}
        )
        return self._repository.save(
            **values,
            document_id=document_id,
            expected_version=old["version"]
            if expected_version is None
            else expected_version,
            publish=kwargs.get("is_published", True),
            owner_id=kwargs.get("owner_id"),
        )

    def delete_document(self, document_id):
        self.initialize()
        return self._repository.delete(document_id)

    def set_document_published(
        self, document_id, published=True, version=None, expected_version=None
    ):
        self.initialize()
        return self._repository.publish(
            document_id, published, version, expected_version
        )

    def list_documents(self):
        self.initialize()
        return self._repository.documents()

    def document_detail(self, document_id, version=None):
        self.initialize()
        return self._repository.detail(document_id, version)

    def get_stats(self) -> dict:
        docs = self.list_documents()
        return {
            "total_documents": len(docs),
            "total_chunks": sum(d["chunks_count"] or 0 for d in docs),
            "backend": self.backend,
            "degraded": not bool(self._vector_store),
            "degraded_reason": None if self._vector_store else "milvus_disabled",
        }

    def health_check(self) -> dict:
        self.initialize()
        healthy = False
        if self._vector_store:
            try:
                self._vector_store.client.list_collections()
                healthy = True
            except Exception:
                pass
        return {
            "backend": self.backend,
            "healthy": healthy,
            "degraded": not healthy,
            "message": "索引连接可用" if healthy else "索引不可用；检索可使用SQL降级",
        }

    def rebuild_index(self):
        self.initialize()
        results = []
        for doc in self.list_documents():
            current = self.document_detail(doc["id"])
            results.append(
                self.update_document(doc["id"], expected_version=current["version"])
            )
        return {"success": True, "status": "queued", "results": results}

    def clear_all(self):
        self.initialize()
        return self._repository.archive_all()

    async def async_add_document(self, *a, **kw):
        return await asyncio.to_thread(self.add_document, *a, **kw)

    async def async_update_document(self, *a, **kw):
        return await asyncio.to_thread(self.update_document, *a, **kw)

    async def async_delete_document(self, *a, **kw):
        return await asyncio.to_thread(self.delete_document, *a, **kw)

    async def async_set_document_published(self, *a, **kw):
        return await asyncio.to_thread(self.set_document_published, *a, **kw)

    async def async_get_stats(self):
        return await asyncio.to_thread(self.get_stats)

    async def async_list_documents(self):
        return await asyncio.to_thread(self.list_documents)

    async def async_rebuild_index(self):
        return await asyncio.to_thread(self.rebuild_index)

    async def async_clear_all(self):
        return await asyncio.to_thread(self.clear_all)
