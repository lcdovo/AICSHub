"""知识库服务包。

由原 knowledge_base.py（约 1470 行单体）按职责拆分而来：

- `bm25.py`             : BM25 关键词评分器（内存降级检索核心组件）
- `cache_invalidation.py`: 查询缓存（QueryCache）与文档变更后的共享缓存失效
- `ingestion.py`        : 分块器（DocumentChunker）与文档生命周期（增/改/删）
- `index_admin.py`      : 索引管理面（初始化加载 / 重建 / 清理 / 统计 / 发布）
- `retrieval.py`        : 混合检索链路（Milvus hybrid / RRF 融合 / 重排）

`KnowledgeBaseService` 由各职责 Mixin 组合而成，对外公开 API
（get_knowledge_base / reset_knowledge_base / KnowledgeBaseService）不变。
"""
from typing import Optional

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
    RetrievalMixin,
    IngestionMixin,
    IndexAdminMixin,
    CacheInvalidationMixin,
):
    """知识库服务 - 管理文档的入库和检索。

    由职责 Mixin 组合：

    - RetrievalMixin         : 混合检索（Milvus 原生 hybrid / 内存双路降级）、RRF 融合、重排
    - IngestionMixin         : 文档分块入库 / 版本更新 / 删除（上传、embedding、持久化）
    - IndexAdminMixin        : 初始化加载、索引重建/清理、统计、健康检查、发布
    - CacheInvalidationMixin : 文档变更后的本地 + Redis 共享缓存立即失效
    """

    # 模型 reranker 的候选上限：本地 CPU (bge-reranker-large) 重排 20 条长文档
    # 实测约 8.5s，超 5s 超时被误判失败；只精排 RRF 融合后的 top N，
    # 在质量与延迟间取平衡（top 10 约 4.3s）。
    RERANK_MAX_CANDIDATES = 10

    def __init__(self):
        self.chunker = DocumentChunker()
        self._documents: list = []
        self._embedding_service = None
        self._vector_store = None
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