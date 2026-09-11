"""长期记忆 / 用户画像服务。

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
            if query and self.max_items > 0:
                profile["recalled"] = await self._recall(query, history)
            profile["memory_source"] = "sqlite"
        except Exception as exc:  # 数据库不可用等
            logger.warning("用户记忆加载失败(user=%s): %s", user_id, exc)
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
                    select(func.count()).select_from(Session).where(Session.user_id == user_id)
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
                .where(Session.user_id == user_id)
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
    _EMBED_CANDIDATE_LIMIT = 20

    async def _recall(
        self, query: str, history: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """向量语义召回优先，不可用回退关键词召回。"""
        if not history:
            return []
        try:
            from app.services.embedding_service import get_embedding_service

            svc = get_embedding_service()
            embedding = svc.get_embedding()
            if embedding is not None:
                # 先做关键词粗筛（当前会话 > 跨会话，含 query 词越多的越靠前），
                # 只对 top 候选做向量编码——一次性 batch，避免 60 条历史拆多次 API。
                query_units = [u for u in query if u.strip() and not u.isspace()]
                scored_candidates = sorted(
                    history,
                    key=lambda h: (
                        h.get("current", False),
                        sum(1 for u in query_units if u in h.get("content", "")),
                    ),
                    reverse=True,
                )[: self._EMBED_CANDIDATE_LIMIT]

                q = await svc.async_encode(query)
                vecs = await svc.async_encode_batch([h["content"] for h in scored_candidates])
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
                        "semantic": round(self._cosine(q, vecs[i]), 3),
                    }
                    for i in top
                ]
        except Exception as exc:
            logger.warning("向量记忆召回不可用，回退关键词: %s", exc)

        query_units = [u for u in query if u.strip() and not u.isspace()]
        scored = sorted(
            history,
            key=lambda h: sum(1 for u in query_units if u in h.get("content", "")),
            reverse=True,
        )
        return [
            {"content": h["content"], "role": h["role"], "semantic": None}
            for h in scored[: self.max_items]
        ]

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