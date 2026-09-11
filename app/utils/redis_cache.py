"""多 worker 共享缓存：Redis 优先 + 进程内内存降级的对象缓存。

背景：uvicorn 多 worker 后，进程内 Dict 缓存各自独立，同一条查询会在每个
worker 重复触发 LLM/embedding 调用。把高频缓存（客服回答、RAG 检索结果）
上移到 Redis，多 worker 共享一份；Redis 不可用时自动降级本地 Dict，
任何后端故障都不影响主链路（沿用全链路的降级约定）。
"""
import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class RedisObjectCache:
    """JSON 对象缓存（带 TTL）。Redis 可用时读写 Redis，否则读写进程内 Dict。"""

    def __init__(self, prefix: str, default_ttl: float = 120.0, max_local: int = 500):
        self._prefix = prefix
        self._ttl = default_ttl
        self._max_local = max_local
        self._local: Dict[str, Tuple[Any, float]] = {}

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def aget(self, key: str) -> Optional[Any]:
        redis = await self._get_redis()
        if redis is not None:
            try:
                raw = await redis.get(self._key(key))
                if raw is not None:
                    return json.loads(raw)
            except Exception as exc:
                logger.warning("Redis 缓存读取失败，降级内存: %s", exc)
        return self._local_get(key)

    async def aset(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        self._local_set(key, value)
        redis = await self._get_redis()
        if redis is not None:
            try:
                ttl_seconds = int(ttl if ttl is not None else self._ttl)
                await redis.setex(
                    self._key(key),
                    ttl_seconds,
                    json.dumps(value, ensure_ascii=False, default=str),
                )
            except Exception as exc:
                logger.warning("Redis 缓存写入失败，仅保留内存: %s", exc)

    @staticmethod
    async def _get_redis():
        from app.utils.database import get_redis

        try:
            return await get_redis()
        except Exception:
            return None

    def _local_get(self, key: str) -> Optional[Any]:
        entry = self._local.get(key)
        if not entry:
            return None
        value, ts = entry
        if time.time() - ts > self._ttl:
            self._local.pop(key, None)
            return None
        return value

    def _local_set(self, key: str, value: Any):
        if len(self._local) >= self._max_local:
            oldest = min(self._local, key=lambda k: self._local[k][1])
            self._local.pop(oldest, None)
        self._local[key] = (value, time.time())

    def clear_local(self):
        self._local.clear()