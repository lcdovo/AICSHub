"""M3知识服务：SQLAlchemy版本事实、持久索引Job、版本过滤双路检索。

公开工厂保留，检索只走版本化链路，不再维护旧检索/启发式执行旁路。
"""
from typing import Optional

from .versioned_search import VersionedSearchMixin
from .lifecycle import LifecycleMixin
from .bm25 import BM25Scorer
from .cache_invalidation import CacheInvalidationMixin, QueryCache
from .ingestion import DocumentChunker
from .retrieval_runtime import RetrievalRuntime

__all__ = [
    "KnowledgeBaseService",
    "get_knowledge_base",
    "reset_knowledge_base",
    "QueryCache",
    "BM25Scorer",
    "DocumentChunker",
    "CacheInvalidationMixin",
]


class KnowledgeBaseService(
    VersionedSearchMixin,
    LifecycleMixin,
    CacheInvalidationMixin,
):
    """生命周期写入SQL版本；检索每次读取SQL发布快照和epoch。"""

    def __init__(self, repository=None, embedding=None, vector_store=None):
        self._repository = repository
        self.chunker = DocumentChunker()
        self._embedding_service = embedding
        self._vector_store = vector_store
        self._initialized = False
        self._query_cache = QueryCache(max_size=500, ttl=600)
        self._retrieval_runtime = RetrievalRuntime()
        self.backend = "memory"
        self.degraded = False
        self.degraded_reason = ""
        self._index_version = 1
        # 模型 reranker不可用时保留RRF排名，见versioned_search。
        from app.services.reranker_service import get_reranker

        self._reranker = get_reranker()


_knowledge_base: Optional[KnowledgeBaseService] = None


def get_knowledge_base() -> KnowledgeBaseService:
    global _knowledge_base
    if _knowledge_base is None:
        _knowledge_base = KnowledgeBaseService()
        _knowledge_base.initialize()
    return _knowledge_base


def reset_knowledge_base():
    global _knowledge_base
    if _knowledge_base:
        _knowledge_base = None
