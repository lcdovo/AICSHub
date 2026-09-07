"""长期记忆 / 用户画像服务。

聚合 SQLite 中的会话数据与用户标签，形成跨会话的用户画像；在基于当前问题时，
优先用向量召回用户相关历史，Embedding/数据库不可用时逐级退化为关键词召回与空画像，
保证离线环境确定可运行。配套的 ``GetUserHistoryTool`` 已接入本服务。
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

        from app.utils.database import async_session, engine
        from app.models.models import Base, Session, Ticket, User

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
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
        from sqlalchemy import select

        from app.utils.database import async_session, engine
        from app.models.models import Base, Message, Session

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_session() as session:
            stmt = (
                select(Message, Session)
                .join(Session, Message.session_id == Session.id)
                .where(Session.user_id == user_id)
                .order_by(Message.created_at.asc())
                .limit(100)
            )
            rows = await session.execute(stmt)
            history = []
            for message, _sess in rows:
                if message.role.value not in ("user", "assistant"):
                    continue
                history.append({
                    "session_id": message.session_id,
                    "role": message.role.value,
                    "content": message.content,
                    "timestamp": message.created_at.isoformat()
                    if message.created_at else "",
                })
            return history

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
                q = await svc.async_encode(query)
                vecs = await svc.async_encode_batch([h["content"] for h in history])
                scored = sorted(
                    range(len(history)),
                    key=lambda i: self._cosine(q, vecs[i]),
                    reverse=True,
                )
                top = scored[: self.max_items]
                return [
                    {
                        "content": history[i]["content"],
                        "role": history[i]["role"],
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