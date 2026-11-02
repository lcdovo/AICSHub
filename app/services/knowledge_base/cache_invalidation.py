"""缓存层：QueryCache（本地 Dict + Redis 共享）与共享缓存失效 Mixin。

自 knowledge_base.py 拆分迁移（原 QueryCache / _flush_shared_caches_sync），
保持"文档变更后进程内 + Redis 前缀键立即失效"的既有语义不变。
"""
import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

from app.config.config import settings

logger = logging.getLogger(__name__)


class QueryCache:
    """查询结果缓存 - 避免重复计算"""

    def __init__(self, max_size: int = 200, ttl: int = 300):
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._max_size = max_size
        self._ttl = ttl

    def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            return None
        value, ts = self._cache[key]
        if time.time() - ts > self._ttl:
            del self._cache[key]
            return None
        return value

    def set(self, key: str, value: Any):
        if len(self._cache) >= self._max_size:
            oldest = min(self._cache, key=lambda k: self._cache[k][1])
            del self._cache[oldest]
        self._cache[key] = (value, time.time())

    async def aget(self, key: str) -> Optional[Any]:
        """异步读取：Redis 共享缓存优先（多 worker 共享，避免重复 embedding），
        Redis 不可用或未命中时回退本地内存。"""
        redis = await self._async_redis()
        if redis is not None:
            try:
                raw = await redis.get(f"kb_cache:{key}")
                if raw is not None:
                    return json.loads(raw)
                self._cache.pop(key, None)
                return None
            except Exception:
                pass
        return self.get(key)

    async def aset(self, key: str, value: Any) -> None:
        """异步写入：本地内存始终维护（同步 search 复用），并尽力写 Redis 共享一份。"""
        self.set(key, value)
        redis = await self._async_redis()
        if redis is not None:
            try:
                await redis.setex(
                    f"kb_cache:{key}",
                    self._ttl,
                    json.dumps(value, ensure_ascii=False, default=str),
                )
            except Exception:
                pass

    @staticmethod
    async def _async_redis():
        from app.utils.database import get_redis

        try:
            return await get_redis()
        except Exception:
            return None

    def clear(self):
        self._cache.clear()

    async def _flush_redis_kb_cache(self) -> None:
        """清 Redis 侧 kb_cache:* 键（文档变更后共享缓存立即失效）。

        使用新建的短生命周期连接而非模块级单例（get_redis），规避 aioredis
        客户端绑定创建它的那个事件循环的问题，可安全地在 asyncio.run /
        to_thread 工作线程中执行。多 worker 下删除 Redis 键对所有进程生效；
        本进程本地 Dict 由调用方 clear() 负责。
        """
        if not settings.REDIS_ENABLED:
            return
        try:
            from redis import asyncio as aioredis

            redis = aioredis.Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
            )
            try:
                cursor = 0
                while True:
                    cursor, keys = await redis.scan(cursor, match="kb_cache:*", count=500)
                    if keys:
                        await redis.delete(*keys)
                    if cursor == 0:
                        break
            finally:
                await redis.aclose()
        except Exception as exc:
            logger.warning("Redis kb_cache 清理失败: %s", exc)

    @staticmethod
    def make_key(query: str, top_k: int, threshold: float) -> str:
        raw = f"{query}|{top_k}|{threshold}"
        return hashlib.md5(raw.encode()).hexdigest()


class CacheInvalidationMixin:
    """文档变更后的共享缓存失效：本地 Dict + Redis 前缀键统一清理。"""

    def _flush_shared_caches_sync(self) -> None:
        """文档变更后统一失效共享缓存：进程内本地 Dict + Redis 前缀键。

        本地 Dict 始终即刻清空；Redis 侧删除 kb_cache:* 与所有
        RedisObjectCache 实例前缀（如 answer:*），保证多 worker 下
        "管理端更新立即生效"。同步写方法实际运行在脚本主线程（无运行中
        loop）或 asyncio.to_thread 工作线程（线程内无 loop），此处
        asyncio.run 恒安全；Redis 清理使用新建短生命周期连接，无跨事件
        循环冲突。防御：若异常路径在运行中事件循环内直接调用同步写方法，
        仅清本地并告警，不抛错阻断主逻辑。
        """
        self._query_cache.clear()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 无运行中 loop：脚本主线程 / asyncio.to_thread 工作线程，可安全 asyncio.run。
            pass
        else:
            # 运行中 loop 内直接调用同步写方法（防御路径）：仅清本地，避免 loop 冲突。
            logger.warning("在异步上下文中调用同步写方法，跳过 Redis 共享缓存清理（仅清本地）")
            return

        async def _flush():
            try:
                await self._query_cache._flush_redis_kb_cache()
            except Exception:
                pass
            try:
                from app.utils.redis_cache import flush_all_redis_object_caches

                await flush_all_redis_object_caches()
            except Exception:
                pass

        try:
            asyncio.run(_flush())
        except Exception as exc:
            logger.warning("共享缓存（Redis）清理失败: %s", exc)