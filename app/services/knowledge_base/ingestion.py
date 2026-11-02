"""文档接入层：分块器与文档入库 Mixin（上传/分块/embedding/持久化）。

自 knowledge_base.py 拆分迁移（原 DocumentChunker / add_document /
_save_to_database / update_document），逻辑保持原样。
"""
import asyncio
import json
import logging
import re
import sqlite3
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


class IngestionMixin:
    """文档入库（上传/分块/embedding/持久化）与更新。"""

    def add_document(
        self,
        title: str,
        content: str,
        category: str = "",
        keywords: Optional[List[str]] = None,
        source: str = "",
        is_published: bool = True,
    ) -> Dict[str, Any]:
        self.initialize()

        metadata = {
            "title": title,
            "category": category,
            "keywords": keywords or [],
            "source": source,
            "chunk_size": self.chunker.chunk_size,
            "chunk_overlap": self.chunker.chunk_overlap,
            "embedding_model": self._embedding_service.backend_name,
            "index_version": self._index_version,
        }

        chunks = self.chunker.chunk_document(content, metadata)
        if not chunks:
            return {"success": False, "message": "文档为空", "chunks_count": 0}

        embeddings = []
        for chunk in chunks:
            chunk_text = chunk["content"]
            embedding = self._embedding_service.encode(chunk_text)
            embeddings.append(embedding)

        doc_record = {
            "id": f"doc_{uuid.uuid4().hex[:12]}",
            "title": title,
            "content": content,
            "category": category,
            "keywords": keywords or [],
            "source": source,
            "chunks": chunks,
            "chunk_count": len(chunks),
            "version": 1,
            "is_published": is_published,
            "index_version": self._index_version,
            "created_at": datetime.now().isoformat(),
        }

        for chunk, embedding in zip(chunks, embeddings):
            chunk["_embedding"] = embedding
            chunk["version"] = 1

        self._save_to_database(doc_record)

        self._documents.append(doc_record)
        self._bm25_built = False
        self._flush_shared_caches_sync()

        if self._vector_store and is_published:
            try:
                docs_for_milvus = []
                for chunk in chunks:
                    milvus_doc = {
                        "id": chunk["id"],
                        "title": title,
                        "content": chunk["content"],
                        "category": category,
                        "keywords": ",".join(keywords) if keywords else "",
                        "source": source,
                    }
                    docs_for_milvus.append(milvus_doc)

                self._vector_store.insert(docs_for_milvus, embeddings)
                logger.info(f"Document '{title}' indexed to Milvus with {len(chunks)} chunks")
            except Exception as e:
                logger.error(f"Failed to index document to Milvus: {e}")
        else:
            logger.info(f"Document '{title}' stored in memory with {len(chunks)} chunks")

        return {
            "success": True,
            "document_id": doc_record["id"],
            "chunks_count": len(chunks),
            "version": 1,
            "status": "published" if is_published else "draft",
        }

    def _save_to_database(self, doc_record: Dict[str, Any]):
        """持久化文档、chunk 和 embedding；本地与集成 profile 使用同一字段契约。"""
        sqlite_path = self._sqlite_path()
        if sqlite_path:
            try:
                with sqlite3.connect(sqlite_path) as conn:
                    conn.execute("PRAGMA foreign_keys=ON")
                    self._ensure_sqlite_schema(conn)
                    cursor = conn.execute(
                        "INSERT INTO knowledge_docs(title,category,content,chunk_count,version,is_published,keywords,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (doc_record["title"], doc_record.get("category", ""), doc_record["content"], len(doc_record.get("chunks", [])), doc_record.get("version", 1), int(doc_record.get("is_published", True)), json.dumps(doc_record.get("keywords", []), ensure_ascii=False), doc_record.get("source", ""), doc_record["created_at"], doc_record["created_at"]),
                    )
                    doc_record["db_id"] = cursor.lastrowid
                    for chunk in doc_record.get("chunks", []):
                        conn.execute(
                            "INSERT INTO knowledge_chunks(document_id,chunk_id,content,embedding,chunk_index,version,is_active,created_at) VALUES(?,?,?,?,?,?,?,?)",
                            (cursor.lastrowid, chunk["id"], chunk["content"], json.dumps(chunk.get("_embedding"), ensure_ascii=False), chunk.get("chunk_index", 0), doc_record.get("version", 1), 1, chunk.get("created_at", doc_record["created_at"])),
                        )
                    conn.commit()
                return
            except Exception as exc:
                logger.error("Failed to save knowledge document to SQLite: %s", exc)
                raise RuntimeError("知识库文档持久化失败") from exc

        try:
            from app.config.config import settings
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
                    sql = """INSERT INTO knowledge_docs
                             (title, category, content, chunk_count, version, is_published, keywords, source, created_at, updated_at)
                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())"""
                    cursor.execute(sql, (
                        doc_record["title"],
                        doc_record.get("category", ""),
                        doc_record["content"],
                        len(doc_record.get("chunks", [])),
                        doc_record.get("version", 1),
                        doc_record.get("is_published", True),
                        json.dumps(doc_record.get("keywords", []), ensure_ascii=False),
                        doc_record.get("source", ""),
                    ))
                    doc_record["db_id"] = cursor.lastrowid
                    for chunk in doc_record.get("chunks", []):
                        cursor.execute("INSERT INTO knowledge_chunks(document_id,chunk_id,content,embedding,chunk_index,version,is_active,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,NOW())", (doc_record["db_id"], chunk["id"], chunk["content"], json.dumps(chunk.get("_embedding")), chunk.get("chunk_index", 0), doc_record.get("version", 1), True))
                conn.commit()
                logger.info(f"Saved document '{doc_record['title']}' to MySQL")
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Failed to save document to MySQL: {e}")
            raise RuntimeError("知识库文档持久化失败") from e

    def update_document(
        self,
        document_id: str,
        *,
        title: Optional[str] = None,
        content: Optional[str] = None,
        category: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        source: Optional[str] = None,
        is_published: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """创建新版本并停用旧 chunk，避免更新期间新旧向量混用。"""
        self.initialize()
        current = next((doc for doc in self._documents if doc.get("id") == document_id), None)
        if not current:
            return {"success": False, "message": "文档不存在"}
        next_version = int(current.get("version", 1)) + 1
        new_doc = self.add_document(
            title=title if title is not None else current["title"],
            content=content if content is not None else current["content"],
            category=category if category is not None else current.get("category", ""),
            keywords=keywords if keywords is not None else current.get("keywords", []),
            source=source if source is not None else current.get("source", ""),
            is_published=is_published if is_published is not None else current.get("is_published", True),
        )
        if not new_doc.get("success"):
            return new_doc
        new_record = self._documents[-1]
        new_record["version"] = next_version
        if self._vector_store:
            try:
                old_chunk_ids = [chunk["id"] for chunk in current.get("chunks", [])]
                if old_chunk_ids:
                    self._vector_store.delete(old_chunk_ids)
            except Exception as exc:
                logger.warning("Failed to remove old document vectors during update: %s", exc)
        # 同步数据库版本号，并停用旧版本；新记录以同版本写入时更新为目标版本。
        sqlite_path = self._sqlite_path()
        if sqlite_path and current.get("db_id"):
            try:
                with sqlite3.connect(sqlite_path) as conn:
                    conn.execute("UPDATE knowledge_docs SET deleted_at=?, is_published=0, updated_at=? WHERE id=?", (datetime.now().isoformat(), datetime.now().isoformat(), current["db_id"]))
                    conn.execute("UPDATE knowledge_chunks SET is_active=0 WHERE document_id=?", (current["db_id"],))
                    conn.execute("UPDATE knowledge_docs SET version=? WHERE id=?", (next_version, new_record.get("db_id")))
                    conn.execute("UPDATE knowledge_chunks SET version=? WHERE document_id=?", (next_version, new_record.get("db_id")))
                    conn.commit()
            except Exception as exc:
                logger.error("Failed to persist document version: %s", exc)
        self._documents = [doc for doc in self._documents if doc is not current]
        self._bm25_built = False
        self._flush_shared_caches_sync()
        new_doc["version"] = next_version
        return {**new_doc, "previous_document_id": document_id}

    async def async_add_document(self, *args, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.add_document, *args, **kwargs)

    async def async_update_document(self, *args, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.update_document, *args, **kwargs)

    def delete_document(self, document_id: str) -> bool:
        """删除文档：Milvus 向量 + 持久化记录 + 共享缓存一并清理。"""
        self.initialize()

        doc_to_remove = None
        for i, doc in enumerate(self._documents):
            if doc["id"] == document_id or doc.get("id") == document_id:
                doc_to_remove = doc
                self._documents.pop(i)
                break

        if not doc_to_remove:
            return False

        if self._vector_store:
            try:
                chunk_ids = [chunk["id"] for chunk in doc_to_remove.get("chunks", [])]
                if chunk_ids:
                    self._vector_store.delete(chunk_ids)
            except Exception as e:
                logger.error(f"Failed to delete document from Milvus: {e}")

        sqlite_path = self._sqlite_path()
        if sqlite_path and doc_to_remove.get("db_id"):
            try:
                with sqlite3.connect(sqlite_path) as conn:
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute("UPDATE knowledge_docs SET deleted_at=?, is_published=0, updated_at=? WHERE id=?", (datetime.now().isoformat(), datetime.now().isoformat(), doc_to_remove["db_id"]))
                    conn.execute("UPDATE knowledge_chunks SET is_active=0 WHERE document_id=?", (doc_to_remove["db_id"],))
                    conn.commit()
            except Exception as exc:
                logger.error("Failed to delete document from SQLite: %s", exc)
                return False
        elif not sqlite_path:
            self._delete_from_mysql(doc_to_remove)

        self._bm25_built = False
        self._flush_shared_caches_sync()

        return True

    def _delete_from_mysql(self, doc_record: Dict[str, Any]):
        """从 MySQL 删除文档 (同步方式)"""
        try:
            from app.config.config import settings
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
                    cursor.execute(
                        "DELETE FROM knowledge_docs WHERE title=%s AND category=%s",
                        (doc_record.get("title", ""), doc_record.get("category", ""))
                    )
                conn.commit()
                logger.info(f"Deleted document '{doc_record.get('title', '')}' from MySQL")
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"Failed to delete from MySQL: {e}")

    async def async_delete_document(self, document_id: str) -> bool:
        return await asyncio.to_thread(self.delete_document, document_id)