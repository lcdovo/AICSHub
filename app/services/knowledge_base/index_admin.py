"""索引与文档管理面：初始化加载、索引重建/清理、统计与健康检查。

自 knowledge_base.py 拆分迁移（原 initialize / _load_from_database /
rebuild_index / clear_all / get_stats / health_check / list_documents /
set_document_published 等），逻辑保持原样。文档生命周期类操作
（add/update/delete）在 ingestion.py，检索链路在 retrieval.py。
"""
import asyncio
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from app.config.config import settings
from app.services.embedding_service import get_embedding_service

from .bm25 import BM25Scorer

logger = logging.getLogger(__name__)


class IndexAdminMixin:
    """索引与文档管理面：持久化恢复、重建/清理、统计、健康检查、发布。"""

    def initialize(self):
        if self._initialized:
            return

        self._embedding_service = get_embedding_service(dim=settings.EMBEDDING_DIM)
        embedding_health = self._embedding_service.health_check()
        if not embedding_health.get("available", False):
            self.degraded = True
            self.degraded_reason = "embedding_unavailable"

        use_milvus = settings.USE_MILVUS
        if use_milvus:
            try:
                from app.utils.milvus_client import get_milvus_client
                self._vector_store = get_milvus_client(
                    host=settings.MILVUS_HOST,
                    port=settings.MILVUS_PORT,
                    dim=settings.EMBEDDING_DIM,
                )
                if self._vector_store.connect():
                    logger.info("KnowledgeBase: Milvus connected")
                    self.backend = "milvus"
                else:
                    logger.warning("KnowledgeBase: Milvus connection failed, using memory")
                    self._vector_store = None
                    self.degraded = True
                    self.degraded_reason = "milvus_unavailable"
            except Exception as e:
                logger.warning(f"KnowledgeBase: Milvus init error: {e}")
                self._vector_store = None
                self.degraded = True
                self.degraded_reason = "milvus_init_failed"
        else:
            logger.info("KnowledgeBase: Using memory-based storage")
            self.degraded = True
            self.degraded_reason = self.degraded_reason or "milvus_disabled"

        self._load_from_database()

        self._initialized = True

    def _sqlite_path(self) -> Optional[str]:
        """解析 sqlite+aiosqlite URL，供启动时同步恢复索引使用。"""
        url = settings.DATABASE_URL
        if not url.startswith("sqlite"):
            return None
        raw = url.split("///", 1)[-1].split("?", 1)[0]
        raw = unquote(raw)
        path = Path(raw)
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)

    def _ensure_sqlite_schema(self, conn: sqlite3.Connection):
        """兼容第 1~3 步已经存在的本地库，补齐 RAG 表/列。"""
        conn.execute("CREATE TABLE IF NOT EXISTS knowledge_docs (id INTEGER PRIMARY KEY AUTOINCREMENT, title VARCHAR(200) NOT NULL, category VARCHAR(50) NOT NULL DEFAULT '', content TEXT NOT NULL, chunk_count INTEGER DEFAULT 0, version INTEGER DEFAULT 1, is_published BOOLEAN DEFAULT 1, keywords JSON, source VARCHAR(500), deleted_at DATETIME, created_at DATETIME, updated_at DATETIME)")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_docs)").fetchall()}
        for name, definition in {
            "keywords": "JSON", "source": "VARCHAR(500)", "deleted_at": "DATETIME",
        }.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE knowledge_docs ADD COLUMN {name} {definition}")
        conn.execute("CREATE TABLE IF NOT EXISTS knowledge_chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL, chunk_id VARCHAR(100) NOT NULL UNIQUE, content TEXT NOT NULL, embedding JSON, chunk_index INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1, is_active BOOLEAN NOT NULL DEFAULT 1, created_at DATETIME, FOREIGN KEY(document_id) REFERENCES knowledge_docs(id) ON DELETE CASCADE)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_document_id ON knowledge_chunks(document_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_active ON knowledge_chunks(is_active)")

    def _load_from_database(self) -> bool:
        """恢复已发布文档及 chunk 内容，保证重启后检索契约不变。"""
        sqlite_path = self._sqlite_path()
        if sqlite_path:
            try:
                with sqlite3.connect(sqlite_path) as conn:
                    conn.execute("PRAGMA foreign_keys=ON")
                    self._ensure_sqlite_schema(conn)
                    rows = conn.execute("SELECT id,title,category,content,chunk_count,version,is_published,keywords,source,created_at FROM knowledge_docs WHERE COALESCE(deleted_at,'')='' ORDER BY id").fetchall()
                    for row in rows:
                        doc_id, title, category, content, chunk_count, version, published, keywords, source, created_at = row
                        chunks = []
                        chunk_rows = conn.execute("SELECT chunk_id,content,embedding,chunk_index,version FROM knowledge_chunks WHERE document_id=? AND is_active=1 ORDER BY chunk_index", (doc_id,)).fetchall()
                        for cid, ccontent, embedding, cindex, cversion in chunk_rows:
                            try:
                                vector = json.loads(embedding) if isinstance(embedding, str) else embedding
                            except (TypeError, json.JSONDecodeError):
                                vector = None
                            item = {"id": cid, "content": ccontent, "chunk_index": cindex, "version": cversion}
                            if vector:
                                item["_embedding"] = vector
                            chunks.append(item)
                        self._documents.append({"id": f"doc_db_{doc_id}", "db_id": doc_id, "title": title, "category": category or "", "content": content or "", "keywords": json.loads(keywords) if keywords else [], "source": source or "", "chunks": chunks, "chunk_count": chunk_count or len(chunks), "version": version or 1, "is_published": bool(published), "created_at": str(created_at or "")})
                        if not chunks and (chunk_count or 0):
                            self.degraded = True
                            self.degraded_reason = self.degraded_reason or "persisted_chunks_missing"
                    logger.info("Loaded %s documents and persisted chunks from SQLite", len(self._documents))
                    return True
            except Exception as exc:
                logger.warning("Failed to load documents from SQLite: %s", exc)

        return self._load_from_mysql()

    def _load_from_mysql(self) -> bool:
        """从 MySQL 加载已发布文档及 chunk（集成 profile）。"""
        try:
            import pymysql

            conn = pymysql.connect(
                host=settings.MYSQL_HOST,
                port=int(settings.MYSQL_PORT),
                user=settings.MYSQL_USER,
                password=settings.MYSQL_PASSWORD,
                database=settings.MYSQL_DATABASE,
                charset="utf8mb4",
            )
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT id, title, category, content, chunk_count, version, is_published, keywords, source, created_at FROM knowledge_docs WHERE COALESCE(deleted_at,'')='' ORDER BY id")
                    rows = cursor.fetchall()
                    for row in rows:
                        chunks = []
                        try:
                            cursor.execute("SELECT chunk_id, content, embedding, chunk_index, version FROM knowledge_chunks WHERE document_id=%s AND is_active=1 ORDER BY chunk_index", (row[0],))
                            for cid, ccontent, embedding, cindex, cversion in cursor.fetchall():
                                vector = json.loads(embedding) if isinstance(embedding, str) else embedding
                                item = {"id": cid, "content": ccontent, "chunk_index": cindex, "version": cversion}
                                if vector: item["_embedding"] = vector
                                chunks.append(item)
                        except Exception:
                            pass
                        self._documents.append({
                            "id": f"doc_mysql_{row[0]}",
                            "db_id": row[0],
                            "title": row[1],
                            "category": row[2],
                            "content": row[3] or "",
                            "keywords": json.loads(row[7]) if isinstance(row[7], str) and row[7] else (row[7] or []),
                            "source": "mysql",
                            "chunks": chunks,
                            "chunk_count": row[4] or len(chunks),
                            "version": row[5] or 1,
                            "is_published": bool(row[6]),
                            "created_at": str(row[9]) if row[9] else "",
                        })
                    if rows:
                        logger.info(f"Loaded {len(rows)} documents from MySQL")
            finally:
                conn.close()
            return True
        except Exception as e:
            logger.warning(f"Failed to load documents from MySQL: {e}")
            return False

    def _refresh_documents_from_database(self) -> None:
        """刷新文档列表，确保外部导入进程写入后管理页面不会继续使用旧缓存。"""
        previous = self._documents
        self._documents = []
        if not self._load_from_database():
            self._documents = previous

    def rebuild_index(self) -> Dict[str, Any]:
        """重建内存降级索引（Milvus 正常时其索引由服务端自维护）。

        内存 BM25 语料直接取自已发布文档，不再维护独立 chunk 副本。
        """
        self.initialize()
        self._bm25 = BM25Scorer()
        published = [d for d in self._documents if d.get("is_published", True)]
        corpus = []
        for doc in published:
            for chunk in doc.get("chunks", []):
                corpus.append(chunk["content"])
        if corpus:
            self._bm25.build_index(corpus)
        self._bm25_built = True
        self._query_cache.clear()
        return {"success": True, "documents": len(published), "chunks": len(corpus), "backend": self.backend, "degraded": self.degraded, "degraded_reason": self.degraded_reason or None}

    def clear_all(self) -> Dict[str, Any]:
        self._documents.clear()
        self._bm25 = BM25Scorer()
        self._bm25_built = False

        if self._vector_store:
            try:
                self._vector_store.drop_collection()
                self._vector_store.connect()
            except Exception as e:
                logger.error(f"Failed to clear Milvus collection: {e}")

        sqlite_path = self._sqlite_path()
        if sqlite_path:
            try:
                with sqlite3.connect(sqlite_path) as conn:
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute("DELETE FROM knowledge_chunks")
                    conn.execute("DELETE FROM knowledge_docs")
                    conn.commit()
            except Exception as exc:
                logger.error("Failed to clear SQLite knowledge base: %s", exc)
        else:
            self._clear_mysql()

        self._flush_shared_caches_sync()
        return {"success": True, "message": "Knowledge base cleared", "backend": self.backend, "degraded": self.degraded, "degraded_reason": self.degraded_reason or None}

    def _clear_mysql(self):
        """清空 MySQL 文档表 (同步方式)"""
        try:
            import pymysql

            conn = pymysql.connect(
                host=settings.MYSQL_HOST,
                port=int(settings.MYSQL_PORT),
                user=settings.MYSQL_USER,
                password=settings.MYSQL_PASSWORD,
                database=settings.MYSQL_DATABASE,
                charset="utf8mb4",
            )
            try:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM knowledge_docs")
                conn.commit()
                logger.info("Cleared all documents from MySQL")
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"Failed to clear MySQL: {e}")

    def get_stats(self) -> Dict[str, Any]:
        self.initialize()

        total_docs = len(self._documents)
        total_chunks = sum(d.get("chunk_count", len(d.get("chunks", []))) for d in self._documents)

        if self._vector_store:
            vector_count = self._vector_store.count()
        else:
            vector_count = total_chunks

        try:
            import pymysql
            conn = pymysql.connect(
                host=settings.MYSQL_HOST,
                port=int(settings.MYSQL_PORT),
                user=settings.MYSQL_USER,
                password=settings.MYSQL_PASSWORD,
                database=settings.MYSQL_DATABASE,
                charset="utf8mb4",
            )
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*), COALESCE(SUM(chunk_count),0) FROM knowledge_docs")
                    row = cursor.fetchone()
                    mysql_docs = row[0] or 0
                    mysql_chunks = row[1] or 0
                    if mysql_docs > 0:
                        total_docs = mysql_docs
                        total_chunks = mysql_chunks
            finally:
                conn.close()
        except Exception:
            pass

        return {
            "total_documents": total_docs,
            "total_chunks": total_chunks,
            "vector_store_count": vector_count,
            "backend": "milvus" if self._vector_store else "memory",
            "collection_name": settings.COLLECTION_NAME if self._vector_store else "memory",
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason or None,
            "index_version": self._index_version,
        }

    def health_check(self) -> Dict[str, Any]:
        """检查实际使用的知识库向量后端，避免依赖 AgentGraph 内部属性。"""
        self.initialize()
        if self._vector_store:
            try:
                healthy, message = self._vector_store.health_check()
                return {
                    "backend": "milvus",
                    "healthy": healthy,
                    "message": message,
                    "degraded": self.degraded,
                    "degraded_reason": self.degraded_reason or None,
                }
            except Exception as exc:
                return {
                    "backend": "milvus",
                    "healthy": False,
                    "message": str(exc),
                    "degraded": True,
                    "degraded_reason": "milvus_health_check_failed",
                }
        return {
            "backend": "memory",
            "healthy": False,
            "message": "Milvus 未连接，使用内存检索降级",
            "degraded": True,
            "degraded_reason": self.degraded_reason or "milvus_unavailable",
        }

    def list_documents(self) -> List[Dict[str, Any]]:
        self.initialize()
        self._refresh_documents_from_database()
        return [{
            "id": doc.get("id", ""),
            "title": doc.get("title", ""),
            "category": doc.get("category", ""),
            "source": doc.get("source", ""),
            "keywords": doc.get("keywords", []),
            "chunks_count": len(doc.get("chunks", [])),
            "version": doc.get("version", 1),
            "status": "published" if doc.get("is_published", True) else "draft",
            "created_at": doc.get("created_at", ""),
        } for doc in self._documents]

    def set_document_published(self, document_id: str, published: bool) -> bool:
        self.initialize()
        doc = next((item for item in self._documents if item.get("id") == document_id), None)
        if not doc:
            return False
        doc["is_published"] = bool(published)
        if self._vector_store:
            try:
                chunk_ids = [chunk["id"] for chunk in doc.get("chunks", [])]
                if not published and chunk_ids:
                    self._vector_store.delete(chunk_ids)
                elif published and chunk_ids:
                    vectors = [chunk.get("_embedding") for chunk in doc.get("chunks", [])]
                    payload = [{"id": chunk["id"], "title": doc["title"], "content": chunk["content"], "category": doc.get("category", ""), "keywords": ",".join(doc.get("keywords", [])), "source": doc.get("source", "")} for chunk in doc.get("chunks", [])]
                    if all(vectors):
                        self._vector_store.insert(payload, vectors)
            except Exception as exc:
                logger.warning("Failed to update vector publication state: %s", exc)
        if doc.get("db_id"):
            sqlite_path = self._sqlite_path()
            if sqlite_path:
                with sqlite3.connect(sqlite_path) as conn:
                    conn.execute("UPDATE knowledge_docs SET is_published=?, updated_at=? WHERE id=?", (int(published), datetime.now().isoformat(), doc["db_id"]))
                    conn.execute("UPDATE knowledge_chunks SET is_active=? WHERE document_id=?", (int(published), doc["db_id"]))
                    conn.commit()
        self._bm25_built = False
        self._flush_shared_caches_sync()
        return True

    async def async_rebuild_index(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self.rebuild_index)

    async def async_clear_all(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self.clear_all)

    async def async_set_document_published(self, document_id: str, published: bool) -> bool:
        return await asyncio.to_thread(self.set_document_published, document_id, published)

    async def async_get_stats(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_stats)

    async def async_list_documents(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.list_documents)