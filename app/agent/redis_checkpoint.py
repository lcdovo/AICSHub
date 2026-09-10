"""
Redis 持久化 Checkpoint Saver - 让 LangGraph interrupt/退款确认跨进程可恢复。

LangGraph 骨架由官方提供，本模块自研的是"用项目现有 Redis 客户端做 checkpoint
存储"，并沿袭项目的降级约定：Redis 不可用时自动回退到内存 InMemorySaver，
保证外部存储不可用绝不影响主链路。

存储布局（prefix 默认 agent_ckpt）：
- agent_ckpt:thread:{thread_id}:{checkpoint_ns}:{checkpoint_id}  <- checkpoint+metadata hash
- agent_ckpt:writes:{thread_id}:{checkpoint_ns}:{checkpoint_id} <- pending writes hash
- agent_ckpt:latest:{thread_id}:{checkpoint_ns}                 <- 最近 checkpoint 指针

序列化使用 LangGraph 内置 JsonPlusSerializer（dumps_typed/loads_typed），
二进制经 base64 编码后写入 Redis，规避 decode_responses=True 的解码问题。
"""
import base64
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Tuple

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.memory import InMemorySaver

logger = logging.getLogger(__name__)

CHECKPOINT_TTL_SECONDS = 86400  # 与会话状态 TTL 一致，一天


class RedisCheckpointSaver(BaseCheckpointSaver):
    """全量 checkpoint 写入 Redis 的 saver；Redis 不可用自动降级内存。

    不做 blob 增量存储，每次把完整 checkpoint（含 channel_values）序列化落库，
    换取实现简单与低 bug 率；以本项目会话量级，存储放大可接受。
    """

    def __init__(
        self,
        *,
        serde: Any = None,
        prefix: str = "agent_ckpt",
        ttl: int = CHECKPOINT_TTL_SECONDS,
    ):
        super().__init__(serde=serde)
        self._prefix = prefix
        self._ttl = ttl
        self._memory = InMemorySaver(serde=self.serde)
        self._redis_ok: Optional[bool] = None

    # ---------- Redis 访问 ----------

    @staticmethod
    def _pack(obj: Any) -> str:
        typ, payload = obj
        return json.dumps({
            "type": typ,
            "value": base64.b64encode(payload).decode("ascii"),
        })

    @classmethod
    def _unpack(cls, raw: str) -> Tuple[str, bytes]:
        data = json.loads(raw)
        return data["type"], base64.b64decode(data["value"])

    async def _get_redis(self):
        """懒获取 Redis 客户端；失败一次后本次进程内记住不可用。"""
        if self._redis_ok is False:
            return None
        try:
            from app.utils.database import get_redis
            redis = await get_redis()
            if redis is None:
                self._redis_ok = False
                return None
            return redis
        except Exception as exc:  # pragma: no cover - 防御性降级
            logger.warning("Redis checkpoint 客户端获取失败，降级内存: %s", exc)
            self._redis_ok = False
            return None

    def _key(self, *parts: str) -> str:
        return ":".join((self._prefix, *parts))

    # ---------- Checkpoint 写入 ----------

    async def aput(
        self,
        config: Any,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Any,
    ) -> Any:
        redis = await self._get_redis()
        if redis is None:
            return await self._memory.aput(config, checkpoint, metadata, new_versions)

        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        ckpt_key = self._key("thread", thread_id, checkpoint_ns, checkpoint_id)
        latest_key = self._key("latest", thread_id, checkpoint_ns)
        parent = config["configurable"].get("checkpoint_id", "")
        field_data = {
            "checkpoint": self._pack(self.serde.dumps_typed(checkpoint)),
            "metadata": self._pack(self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))),
            "parent": parent,
        }
        try:
            pipe = redis.pipeline()
            pipe.hset(ckpt_key, mapping=field_data)
            pipe.expire(ckpt_key, self._ttl)
            pipe.set(latest_key, checkpoint_id, ex=self._ttl)
            await pipe.execute()
        except Exception as exc:
            logger.warning("Redis checkpoint 写入失败，降级内存: %s", exc)
            self._redis_ok = False
            return await self._memory.aput(config, checkpoint, metadata, new_versions)

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: Any,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        redis = await self._get_redis()
        if redis is None:
            return await self._memory.aput_writes(config, writes, task_id, task_path)

        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        writes_key = self._key("writes", thread_id, checkpoint_ns, checkpoint_id)
        mapping: Dict[str, str] = {}
        for idx, (channel, value) in enumerate(writes):
            payload = self.serde.dumps_typed((task_id, channel, value, task_path))
            mapping[f"{task_id}:{idx}"] = self._pack(payload)
        try:
            pipe = redis.pipeline()
            pipe.hset(writes_key, mapping=mapping)
            pipe.expire(writes_key, self._ttl)
            await pipe.execute()
        except Exception as exc:
            logger.warning("Redis checkpoint writes 写入失败，降级内存: %s", exc)
            self._redis_ok = False
            return await self._memory.aput_writes(config, writes, task_id, task_path)

    # ---------- Checkpoint 读取 ----------

    async def aget_tuple(self, config: Any) -> Optional[CheckpointTuple]:
        redis = await self._get_redis()
        if redis is None:
            return await self._memory.aget_tuple(config)

        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"].get("checkpoint_id", "")
        try:
            if not checkpoint_id:
                checkpoint_id = await redis.get(
                    self._key("latest", thread_id, checkpoint_ns)
                ) or ""
            if not checkpoint_id:
                return None

            ckpt_key = self._key("thread", thread_id, checkpoint_ns, checkpoint_id)
            data = await redis.hgetall(ckpt_key)
            if not data:
                return None

            checkpoint = self.serde.loads_typed(self._unpack(data["checkpoint"]))
            metadata = self.serde.loads_typed(self._unpack(data["metadata"]))
            parent = data.get("parent") or ""

            pending_writes = await self._load_pending_writes(
                redis, thread_id, checkpoint_ns, checkpoint_id
            )
            result_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            }
            parent_config = (
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent,
                    }
                }
                if parent
                else None
            )
            return CheckpointTuple(
                config=result_config,
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=parent_config,
                pending_writes=pending_writes,
            )
        except Exception as exc:
            logger.warning("Redis checkpoint 读取失败，降级内存: %s", exc)
            self._redis_ok = False
            return await self._memory.aget_tuple(config)

    async def _load_pending_writes(
        self,
        redis: Any,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> List[Tuple[str, str, Any]]:
        writes_key = self._key("writes", thread_id, checkpoint_ns, checkpoint_id)
        items = await redis.hgetall(writes_key)
        writes: List[Tuple[int, Tuple[str, str, Any]]] = []
        for field, raw in items.items():
            try:
                task_id, idx = field.rsplit(":", 1)
                task_id, channel, value, _ = self.serde.loads_typed(self._unpack(raw))
                writes.append((int(idx), (task_id, channel, value)))
            except Exception:  # pragma: no cover - 坏数据跳过
                logger.warning("跳过损坏的 checkpoint write: %s/%s", checkpoint_id, field)
        writes.sort(key=lambda item: item[0])
        return [payload for _, payload in writes]

    async def alist(
        self,
        config: Any,
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Any = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        redis = await self._get_redis()
        if redis is None:
            async for item in self._memory.alist(
                config, filter=filter, before=before, limit=limit
            ):
                yield item
            return

        if not config:
            return
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        try:
            prefix = self._key("thread", thread_id, checkpoint_ns) + ":"
            cursor = 0
            collected: List[CheckpointTuple] = []
            async for ckpt_key in redis.scan_iter(match=f"{prefix}*", count=100):
                checkpoint_id = ckpt_key.rsplit(":", 1)[-1]
                ckpt_config = {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": checkpoint_id,
                    }
                }
                try:
                    item = await self.aget_tuple(ckpt_config)
                except Exception:  # pragma: no cover
                    item = None
                if item is not None:
                    collected.append(item)
            collected.sort(key=lambda t: t.checkpoint.get("ts", ""))
            collected = collected[-limit:] if limit is not None else collected
            for item in collected:
                yield item
        except Exception as exc:
            logger.warning("Redis checkpoint 列表失败，降级内存: %s", exc)
            self._redis_ok = False
            async for item in self._memory.alist(
                config, filter=filter, before=before, limit=limit
            ):
                yield item