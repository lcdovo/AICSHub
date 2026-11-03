"""M3知识服务：SQLAlchemy版本事实、持久索引Job、版本过滤双路检索。

公开工厂保持兼容；旧Mixin名仅为兼容导入，不再维护手写DDL或按标题删除。
"""
from typing import Optional

from .versioned_search import VersionedSearchMixin
from .lifecycle import LifecycleMixin
from .bm25 import BM25Scorer
from .cache_invalidation import CacheInvalidationMixin, QueryCache
from .ingestion import DocumentChunker, IngestionMixin
from .index_admin import IndexAdminMixin
from .retrieval import RetrievalMixin

__all__ = [
    "KnowledgeBaseService",
    "get_knowledge_base",
    "reset_knowledge_base",
    "QueryCache",
    "BM25Scorer",
    "DocumentChunker",
    "IngestionMixin",
    "IndexAdminMixin",
    "RetrievalMixin",
    "CacheInvalidationMixin",
]


class KnowledgeBaseService(
    VersionedSearchMixin,
    LifecycleMixin,
    RetrievalMixin,
    CacheInvalidationMixin,
):
    """生命周期写入SQL版本；检索每次读取SQL发布快照和epoch。"""

    # 模型 reranker 的候选上限：本地 CPU (bge-reranker-large) 重排 20 条长文档
    # 实测约 8.5s，超 5s 超时被误判失败；只精排 RRF 融合后的 top N，
    # 在质量与延迟间取平衡（top 10 约 4.3s）。
    RERANK_MAX_CANDIDATES = 10

    def __init__(self, repository=None, embedding=None, vector_store=None):
        self._repository = repository
        self.chunker = DocumentChunker()
        self._documents: list = []
        self._embedding_service = embedding
        self._vector_store = vector_store
        self._initialized = False
        self._query_cache = QueryCache(max_size=500, ttl=600)
        self._bm25 = BM25Scorer()
        self._bm25_built = False
        self.backend = "memory"
        self.degraded = False
        self.degraded_reason = ""
        self._index_version = 1
        # 模型 reranker（外部 API → 本地 bge-reranker-large）；未配置则回退启发式。
        from app.services.reranker_service import get_reranker

        self._reranker = get_reranker()
        # 最近一次检索实际使用的重排后端，写入结果元数据便于观测。
        self._rerank_backend = "none"


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