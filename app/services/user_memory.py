"""长期记忆 / 用户画像服务 —— 长期记忆的唯一入口（LangMem 平行链路已移除）。

聚合当前关系库（MySQL/SQLite 双支持）中的会话数据与用户标签，形成跨会话的
用户画像；在基于当前问题时，优先用向量召回用户相关历史，Embedding/数据库不可用时
逐级退化为关键词召回与空画像，保证离线环境确定可运行。
配套的 ``GetUserHistoryTool`` 已接入本服务。表结构由启动期 init_database()/Alembic
统一保证，本服务不在请求路径重复执行 DDL。
"""
import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class UserMemoryService:
    """用户画像聚合与历史召回。"""

    def __init__(self, max_items: int = 5):
        self.max_items = max(0, min(max_items, 50))

    async def get_user_profile(
        self,
        user_id: int,
        query: str = "",
        session_id: str = "",
    ) -> Dict[str, Any]:
        """返回画像结构；任何后端故障都降级为可信的默认结构而非抛错。"""
        profile: Dict[str, Any] = {
            "user_id": user_id,
            "user_level": "normal",
            "total_sessions": 0,
            "total_tickets": 0,
            "tags": [],
            "history": [],
            "recalled": [],
            "memory_source": "none",
        }
        if not user_id or user_id <= 0:
            return profile
        try:
            await self._load_profile(profile, user_id)
            history = await self._load_history(user_id, session_id)
            profile["history"] = history[-self.max_items:] if self.max_items > 0 else []
            profile["memory_source"] = "sql_history"
            if query and self.max_items > 0:
                recalled, recall_backend = await self._recall(query, history)
                profile["recalled"] = recalled
                self._record_recall_metrics(profile, recall_backend)
        except Exception as exc:  # 数据库不可用等
            logger.warning("用户记忆加载失败(user=%s): %s", user_id, type(exc).__name__)
            profile["memory_source"] = "degraded"
        return profile

    async def _load_profile(self, profile: Dict[str, Any], user_id: int) -> None:
        from sqlalchemy import func, select

        from app.utils.database import async_session
        from app.models.models import User, Session, Ticket

        async with async_session() as session:
            user = await session.get(User, user_id)
            if user is not None:
                level = user.level
                profile["user_level"] = level.value if hasattr(level, "value") else (level or "normal")
                profile["tags"] = list(user.tags or [])
            profile["total_sessions"] = (
                await session.scalar(
                    select(func.count()).select_from(Session).where(Session.user_id == user_id, Session.deleted_at.is_(None))
                )
            ) or 0
            profile["total_tickets"] = (
                await session.scalar(
                    select(func.count()).select_from(Ticket).where(Ticket.user_id == user_id)
                )
            ) or 0

    async def _load_history(
        self, user_id: int, session_id: str
    ) -> List[Dict[str, Any]]:
        """取该用户最近的历史消息（优先当前会话，倒序取最近 60 条）。

        原实现对 user 全量历史按 created_at **正序** limit 100，拉的是最早的
        消息；既不是"最近上下文"，embedding 候选还必然超限。这里改为
        created_at desc + id desc（时间戳可能相同，用 id 打平）取最近消息，
        控制 embedding 召回候选规模。
        """
        from sqlalchemy import desc, select

        from app.utils.database import async_session
        from app.models.models import Message, Session

        async with async_session() as session:
            stmt = (
                select(Message, Session)
                .join(Session, Message.session_id == Session.id)
                .where(Session.user_id == user_id, Session.deleted_at.is_(None))
                .order_by(desc(Message.created_at), desc(Message.id))
                .limit(60)
            )
            rows = await session.execute(stmt)
            history = []
            for message, sess in rows:
                if message.role.value not in ("user", "assistant"):
                    continue
                history.append({
                    "session_id": message.session_id,
                    "message_id": message.id,
                    "source_type": "historical_user_statement" if message.role.value=="user" else "historical_assistant_response",
                    "current": message.session_id == session_id,
                    "role": message.role.value,
                    "content": message.content,
                    "timestamp": message.created_at.isoformat()
                    if message.created_at else "",
                })
            # 翻成时间正序，向量召回/关键词排序内部再按相关性重新评分。
            history.reverse()
            return history

    # 向量候选经关键词粗筛后的上限，控制在单次 embedding batch 内。
    # 20 → 8：本地 CPU 批量编码约 650ms/条，20 条≈13s；最终只取 top max_items(5)，
    # 粗筛已按相关度排序，截断低相关候选对召回质量影响可忽略。
    _EMBED_CANDIDATE_LIMIT = 8

    @staticmethod
    def _query_units(query: str) -> List[str]:
        """query 切分为去重 bigram；不足两字时回退单字。

        单字匹配虚命中率高（"退" 命中 "让步"），bigram 在零依赖下显著更准。
        """
        text = "".join(query.split())
        if not text:
            return []
        if len(text) < 2:
            return [text]
        return list(dict.fromkeys(text[i : i + 2] for i in range(len(text) - 1)))

    @staticmethod
    def _record_recall_metrics(profile: Dict[str, Any], backend: str) -> None:
        """上报召回度量（memory_source 分布 / 空召回率 / 召回后端），失败静默。"""
        try:
            from app.utils.tracking import get_tracer

            get_tracer().record_memory(
                source=str(profile.get("memory_source") or "unknown"),
                recalled_count=len(profile.get("recalled") or []),
                backend=backend,
            )
        except Exception:
            pass

    async def _recall(
        self, query: str, history: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], str]:
        """向量语义召回优先，不可用回退关键词召回。返回 (召回结果, 后端标识)。

        候选仅取**跨会话的用户消息**：当前会话内容已在对话上下文中，重复召回
        浪费候选名额；assistant 的工具模板长回复是编码噪声，用户画像记的是
        "用户说过什么"。
        """
        candidates = [
            h for h in history
            if h.get("role") == "user" and not h.get("current", False)
        ]
        if not candidates:
            return [], "none"
        units = self._query_units(query)
        try:
            from app.services.embedding_service import get_embedding_service

            svc = get_embedding_service()
            embedding = svc.get_embedding()
            if embedding is not None:
                # 先做 bigram 粗筛，只对 top 候选做向量编码——一次性 batch，
                # 避免 60 条历史拆多次 API。
                scored_candidates = sorted(
                    candidates,
                    key=lambda h: sum(1 for u in units if u in h.get("content", "")),
                    reverse=True,
                )[: self._EMBED_CANDIDATE_LIMIT]

                q = await svc.async_encode_query(query)
                vecs = await self._encode_with_cache(
                    svc, [h["content"] for h in scored_candidates]
                )
                ranked = sorted(
                    range(len(scored_candidates)),
                    key=lambda i: self._cosine(q, vecs[i]),
                    reverse=True,
                )
                top = ranked[: self.max_items]
                return [
                    {
                        "content": scored_candidates[i]["content"],
                        "role": scored_candidates[i]["role"],
                        "message_id": scored_candidates[i]["message_id"],
                        "session_id": scored_candidates[i]["session_id"],
                        "source_type": scored_candidates[i]["source_type"],
                        "semantic": round(self._cosine(q, vecs[i]), 3),
                    }
                    for i in top
                ], "vector"
        except Exception as exc:
            logger.warning("向量记忆召回不可用，回退关键词: %s", type(exc).__name__)

        scored = sorted(
            candidates,
            key=lambda h: sum(1 for u in units if u in h.get("content", "")),
            reverse=True,
        )
        return [
            {**h, "semantic": None}
            for h in scored[: self.max_items]
        ], "keyword"

    _VECTOR_CACHE_TTL = 3 * 24 * 3600  # 历史内容向量 3 天有效；内容不可变，过期防 Redis 膨胀

    async def _encode_with_cache(
        self, embed, contents: List[str]
    ) -> List[List[float]]:
        """批量编码历史内容，优先命中 Redis 向量缓存（um:vec:{model}:{sha256}）。

        历史消息内容在连续对话中滚动复用，向量由内容唯一决定（与 query 无关），
        缓存可跨轮/跨 worker/跨重启命中；miss 的才批量请求 Embedding API，
        结果写回缓存。任何 Redis 故障降级为直接编码，不阻断向量召回。
        """
        import hashlib
        import json

        redis = None
        try:
            from app.utils.database import get_redis

            redis = await get_redis()
        except Exception as exc:
            logger.warning("用户记忆向量缓存不可用，直接编码: %s", exc)

        def cache_key(text: str) -> str:
            model = getattr(embed, "model", "embed")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            return f"um:vec:{model}:{digest}"

        vectors: Dict[str, List[float]] = {}
        missed: List[str] = []
        for text in contents:
            cached = None
            if redis is not None:
                try:
                    cached = await redis.get(cache_key(text))
                except Exception as exc:
                    logger.warning("用户记忆向量缓存读取失败: %s", exc)
                    redis = None
            if cached is not None:
                vectors[text] = json.loads(cached)
            else:
                missed.append(text)

        if missed:
            new_vectors = await embed.async_encode_batch(missed)
            for text, vec in zip(missed, new_vectors):
                vectors[text] = vec
                if redis is not None:
                    try:
                        await redis.setex(
                            cache_key(text),
                            self._VECTOR_CACHE_TTL,
                            json.dumps(vec),
                        )
                    except Exception as exc:
                        logger.warning("用户记忆向量缓存写入失败: %s", exc)
                        redis = None

        return [vectors[t] for t in contents]

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)


_service_instance: Optional[UserMemoryService] = None


def get_user_memory_service() -> UserMemoryService:
    global _service_instance
    if _service_instance is None:
        from app.config.config import settings

        _service_instance = UserMemoryService(
            max_items=int(getattr(settings, "USER_MEMORY_MAX_ITEMS", 5) or 5)
        )
    return _service_instance