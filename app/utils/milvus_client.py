"""
Milvus 客户端封装
提供向量集合管理、插入、搜索等核心能力
支持健康检查和优雅降级
"""
import logging
import time
import json
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

VECTOR_FIELD = "embedding"
PRIMARY_FIELD = "id"
FULLTEXT_FIELD = "content"
FULLTEXT_INDEX_NAME = "content_fulltext"


def _get_default_collection_name() -> str:
    try:
        from app.config.config import settings
        return settings.COLLECTION_NAME
    except Exception:
        return "customer_service_knowledge"


class MilvusClient:
    """Milvus 向量数据库客户端"""

    def __init__(self, host: str = "localhost", port: int = 19530, dim: int = 1024, collection_name: str = None):
        self.host = host
        self.port = port
        self.dim = dim
        self.collection_name = collection_name or _get_default_collection_name()
        self._connected = False
        self._collection = None
        self._client = None
        self._fulltext_ok: Optional[bool] = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        try:
            from pymilvus import connections, utility

            self._client = connections.connect(
                alias="default",
                host=self.host,
                port=str(self.port),
                timeout=10,
            )

            if not utility.has_collection(self.collection_name):
                self._create_collection()
            else:
                self._load_collection()
                self._ensure_fulltext_index()

            self._connected = True
            logger.info(f"Connected to Milvus at {self.host}:{self.port}, collection: {self.collection_name}")
            return True

        except ImportError:
            logger.warning("pymilvus not installed, Milvus unavailable")
            self._connected = False
            return False
        except Exception as e:
            logger.warning(f"Failed to connect Milvus: {e}")
            self._connected = False
            return False

    def disconnect(self):
        try:
            from pymilvus import connections
            connections.disconnect("default")
        except Exception:
            pass
        self._connected = False
        self._collection = None

    def _create_collection(self):
        from pymilvus import CollectionSchema, FieldSchema, DataType, Collection

        fields = [
            FieldSchema(name=PRIMARY_FIELD, dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name=VECTOR_FIELD, dtype=DataType.FLOAT_VECTOR, dim=self.dim),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="keywords", dtype=DataType.VARCHAR, max_length=1024),
            FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="updated_at", dtype=DataType.VARCHAR, max_length=64),
        ]

        schema = CollectionSchema(fields, description="Customer Service Knowledge Base")
        self._collection = Collection(name=self.collection_name, schema=schema)

        index_params = {
            "metric_type": "COSINE",
            "index_type": "HNSW",
            "params": {"M": 16, "efConstruction": 256},
        }
        self._collection.create_index(field_name=VECTOR_FIELD, index_params=index_params)

        self._collection.load()
        # Milvus 2.5 原生 BM25 全文索引：混合检索（dense+fulltext）的关键前提。
        # 单独 try/except，实现/analyzer 不可用不影响向量索引可用性。
        try:
            self._collection.create_index(
                field_name=FULLTEXT_FIELD,
                index_name=FULLTEXT_INDEX_NAME,
                index_params=self._fulltext_index_params(),
            )
            logger.info(f"Created FULLTEXT(BM25) index on {FULLTEXT_FIELD}: {self.collection_name}")
        except Exception as exc:
            logger.warning(f"Failed to create FULLTEXT index, BM25 route unavailable: {exc}")
        logger.info(f"Created collection: {self.collection_name} (dim={self.dim})")

    @staticmethod
    def _fulltext_index_params() -> Dict[str, Any]:
        """BM25 全文索引参数；中文场景优先 jieba analyzer，服务端不可用时回退 standard。"""
        return {
            "index_type": "FULLTEXT",
            "metric_type": "BM25",
            "params": {
                "enable_analyzer": True,
                "analyzer_params": {"type": "standard", "stop_words": ["的", "了", "吗", "呢", "啊"]},
            },
        }

    def _ensure_fulltext_index(self):
        """为既有 collection 补齐 FULLTEXT 索引（幂等），使原生 BM25 检索可用。"""
        try:
            names = {str(idx.index_name) for idx in self._collection.indexes}
            if FULLTEXT_INDEX_NAME in names:
                return
            self._collection.create_index(
                field_name=FULLTEXT_FIELD,
                index_name=FULLTEXT_INDEX_NAME,
                index_params=self._fulltext_index_params(),
            )
            logger.info(f"Ensured FULLTEXT(BM25) index on {FULLTEXT_FIELD}: {self.collection_name}")
        except Exception as exc:
            logger.warning(f"Failed to ensure FULLTEXT index, BM25 route unavailable: {exc}")

    def _load_collection(self):
        from pymilvus import Collection
        self._collection = Collection(name=self.collection_name)
        self._collection.load()
        logger.info(f"Loaded collection: {self.collection_name}")

    def _ensure_collection(self):
        """确保集合存在，如不存在则创建"""
        if self._collection is None:
            try:
                from pymilvus import utility
                if utility.has_collection(self.collection_name):
                    self._load_collection()
                else:
                    self._create_collection()
                logger.info(f"Collection ensured: {self.collection_name}")
            except Exception as e:
                logger.error(f"Failed to ensure collection: {e}")
                raise

    def insert(self, docs: List[Dict[str, Any]], embeddings: List[List[float]]) -> int:
        """插入文档及其向量"""
        if not self._connected:
            raise RuntimeError("Milvus not connected")
        self._ensure_collection()

        try:
            data = []
            for i, doc in enumerate(docs):
                row = {
                    PRIMARY_FIELD: doc.get("id", str(i)),
                    VECTOR_FIELD: embeddings[i] if i < len(embeddings) else [0.0] * self.dim,
                    "title": doc.get("title", "")[:512],
                    "content": doc.get("content", "")[:65535],
                    "category": doc.get("category", "")[:128],
                    "keywords": self._serialize_keywords(doc.get("keywords", ""))[:1024],
                    "source": doc.get("source", "")[:256],
                    "updated_at": doc.get("updated_at", str(time.time()))[:64],
                }
                data.append(row)

            self._collection.insert(data)
            self._collection.flush()
            logger.info(f"Inserted {len(data)} documents into Milvus")
            return len(data)

        except Exception as e:
            logger.error(f"Failed to insert into Milvus: {e}")
            raise

    @staticmethod
    def _serialize_keywords(keywords: Any) -> str:
        if isinstance(keywords, list):
            return ",".join(str(k) for k in keywords)
        if isinstance(keywords, str):
            return keywords
        return str(keywords) if keywords else ""

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        score_threshold: float = 0.3,
        filters: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """向量相似度搜索"""
        if not self._connected:
            raise RuntimeError("Milvus not connected")
        self._ensure_collection()

        try:
            search_params = {
                "metric_type": "COSINE",
                "params": {"ef": 64},
            }

            expr = filters or ""

            results = self._collection.search(
                data=[query_embedding],
                anns_field=VECTOR_FIELD,
                param=search_params,
                limit=top_k,
                expr=expr,
                output_fields=["title", "content", "category", "keywords", "source", "updated_at"],
            )

            docs = []
            for hits in results:
                for hit in hits:
                    score = hit.score
                    if score >= score_threshold:
                        doc = hit.entity
                        docs.append({
                            "id": getattr(doc, PRIMARY_FIELD, hit.id),
                            "title": getattr(doc, "title", ""),
                            "content": getattr(doc, "content", ""),
                            "category": getattr(doc, "category", ""),
                            "keywords": getattr(doc, "keywords", ""),
                            "source": getattr(doc, "source", ""),
                            "updated_at": getattr(doc, "updated_at", ""),
                            "score": score,
                            "vector_score": round(score, 4),
                        })

            return docs

        except Exception as e:
            logger.error(f"Milvus search error: {e}")
            return []

    def _fulltext_available(self) -> bool:
        """FULLTEXT 索引是否可用（连接期探测一次并缓存，避免每查一次索引列表）。"""
        if self._fulltext_ok is None:
            try:
                names = {str(idx.index_name) for idx in self._collection.indexes}
                self._fulltext_ok = FULLTEXT_INDEX_NAME in names
            except Exception:
                return False
        return self._fulltext_ok

    def hybrid_search(
        self,
        query_embedding: List[float],
        query_text: str,
        top_k: int = 5,
        candidate_multiplier: int = 4,
    ) -> List[Dict[str, Any]]:
        """Dense 向量 + BM25 全文的混合检索，服务器端 RRF 融合（k=60）。

        取代应用层自写 BM25 打分与 RRF 合并：索引与评分都在 Milvus 内部完成，
        消除进程内全量语料驻留与多副本索引不一致问题。
        """
        if not self._connected:
            raise RuntimeError("Milvus not connected")
        self._ensure_collection()

        dense_limit = max(top_k * candidate_multiplier, 16)
        from pymilvus import AnnSearchRequest, RRFRanker

        reqs: List[Any] = [
            AnnSearchRequest(
                data=[query_embedding],
                anns_field=VECTOR_FIELD,
                param={"metric_type": "COSINE", "params": {"ef": 64}},
                limit=dense_limit,
            ),
        ]
        if self._fulltext_available():
            reqs.append(AnnSearchRequest(
                data=[query_text],
                anns_field=FULLTEXT_FIELD,
                param={"metric_type": "BM25"},
                limit=dense_limit,
            ))

        try:
            hits = self._collection.hybrid_search(
                reqs=reqs,
                rerank=RRFRanker(k=60),
                limit=dense_limit,
                output_fields=["title", "content", "category", "keywords", "source", "updated_at"],
            )
            docs, seen = [], set()
            for row in hits:
                for hit in row:
                    entity = hit.entity
                    doc_id = getattr(entity, PRIMARY_FIELD, hit.id)
                    if doc_id in seen:
                        continue
                    seen.add(doc_id)
                    docs.append({
                        "id": doc_id,
                        "title": getattr(entity, "title", ""),
                        "content": getattr(entity, "content", ""),
                        "category": getattr(entity, "category", ""),
                        "keywords": getattr(entity, "keywords", ""),
                        "source": getattr(entity, "source", ""),
                        "updated_at": getattr(entity, "updated_at", ""),
                        "score": float(hit.score),
                        "vector_score": 0.0,
                        "bm25_score": 0.0,
                        "final_score": round(float(hit.score), 6),
                    })
            return docs[: top_k * candidate_multiplier]
        except Exception as e:
            logger.error(f"Milvus hybrid search error: {e}")
            raise

    def count(self) -> int:
        try:
            self._ensure_collection()
            return self._collection.num_entities
        except Exception:
            return 0

    def delete(self, ids: List[str]):
        if not self._connected:
            raise RuntimeError("Milvus not connected")
        self._ensure_collection()
        try:
            # 主键为 VARCHAR，Milvus 表达式中的值必须使用双引号包裹。
            escaped_ids = [str(item).replace('\\', '\\\\').replace('"', '\\"') for item in ids]
            expr = f'{PRIMARY_FIELD} in [{",".join(json.dumps(item, ensure_ascii=False) for item in escaped_ids)}]'
            self._collection.delete(expr)
            self._collection.flush()
            logger.info(f"Deleted {len(ids)} documents from Milvus")
        except Exception as e:
            logger.error(f"Milvus delete error: {e}")
            raise

    def drop_collection(self):
        if not self._connected:
            return
        try:
            from pymilvus import utility
            utility.drop_collection(self.collection_name)
            self._collection = None
            self._connected = False
            self._fulltext_ok = None
            logger.info(f"Dropped collection: {self.collection_name}")
        except Exception as e:
            logger.error(f"Failed to drop collection: {e}")

    def health_check(self) -> Tuple[bool, str]:
        if not self._connected:
            return False, "Not connected"
        try:
            from pymilvus import utility
            if utility.has_collection(self.collection_name):
                return True, "OK"
            return False, "Collection not found"
        except Exception as e:
            return False, str(e)


_milvus_client: Optional[MilvusClient] = None


def get_milvus_client(host: str = "localhost", port: int = 19530, dim: int = 1024) -> MilvusClient:
    global _milvus_client
    if _milvus_client is None:
        _milvus_client = MilvusClient(host=host, port=port, dim=dim)
    return _milvus_client


def reset_milvus_client():
    global _milvus_client
    if _milvus_client:
        _milvus_client.disconnect()
        _milvus_client = None
