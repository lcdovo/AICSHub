"""Redis Checkpoint Saver 单元测试：写读往返、pending writes、降级内存。

验证 RedisCheckpointSaver 不依赖真实 Redis：
1. Redis 不可用时自动降级 InMemorySaver，行为与之前一致
2. 提供假 Redis（内存 dict 模拟 hset/hgetall/pipeline）时，checkpoint 可写可读
3. pending writes（如 interrupt 的 __interrupt__ 写入）正确累计与恢复
"""
import asyncio

import pytest

from app.agent.redis_checkpoint import RedisCheckpointSaver
from app.agent.state import AgentState


def _config(thread_id="t1", checkpoint_ns="", checkpoint_id=""):
    cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}}
    if checkpoint_id:
        cfg["configurable"]["checkpoint_id"] = checkpoint_id
    return cfg


def _checkpoint(cid="cp-1", state=None):
    return {
        "v": 1,
        "ts": "2026-09-10T00:00:00+00:00",
        "id": cid,
        "channel_values": {"state": state or AgentState(session_id="s1", user_id=1, user_message="hi")},
        "channel_versions": {"state": 1},
        "versions_seen": {},
        "pending_sends": [],
    }


class FakePipeline:
    def __init__(self, store):
        self._store = store
        self._ops = []

    def hset(self, key, mapping=None, **kwargs):
        self._ops.append(("hset", key, mapping))
        return self

    def expire(self, *a):
        self._ops.append(("expire", a))
        return self

    def set(self, *a, **kw):
        self._ops.append(("set", a))
        return self

    async def execute(self):
        for op in self._ops:
            if op[0] == "hset":
                key, mapping = op[1], op[2]
                d = self._store.setdefault(key, {})
                d.update(mapping)
            elif op[0] == "expire":
                pass
            elif op[0] == "set":
                key, value = op[1][0], op[1][1]
                self._store[key] = value
        return [True] * len(self._ops)


class FakeRedis:
    """内存假 Redis：覆盖本 saver 用到的命令。"""

    def __init__(self):
        self.store = {}
        self._pings = []

    def pipeline(self):
        return FakePipeline(self.store)

    async def hset(self, key, mapping=None, **kwargs):
        d = self.store.setdefault(key, {})
        d.update(mapping or {})
        return 1

    async def hgetall(self, key):
        return dict(self.store.get(key, {}))

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    async def scan_iter(self, match=None, count=100):
        prefix = match.split("*")[0] if match else ""
        for key in list(self.store):
            if key.startswith(prefix):
                yield key


class TestRedisLossFallback:
    async def test_redis_unavailable_falls_back_to_memory(self):
        saver = RedisCheckpointSaver()
        saver._redis_ok = False  # 等价于 Redis 不可用
        cp = await saver.aget_tuple(_config(thread_id="missing"))
        assert cp is None

    async def test_memory_fallback_roundtrip(self):
        saver = RedisCheckpointSaver()
        saver._redis_ok = False
        cfg = await saver.aput(
            _config(thread_id="t9"), _checkpoint(cid="cp-9"), {"step": 1},
            {"state": 1},  # new_versions 须与 channel_versions 对齐，InMemorySaver 靠它存值
        )
        tup = await saver.aget_tuple(cfg)
        assert tup is not None
        assert tup.checkpoint["id"] == "cp-9"
        state = tup.checkpoint["channel_values"]["state"]
        assert state.user_message == "hi"


class TestRedisRoundtrip:
    async def _saver_with_fake_redis(self):
        saver = RedisCheckpointSaver()
        fake = FakeRedis()
        # 绕过 get_redis：直接注入假客户端与"可用"状态
        saver._redis_ok = True
        saver._fake = fake

        async def _fake_get_redis():
            return fake

        import app.agent.redis_checkpoint as mod
        orig = mod.get_redis if hasattr(mod, "get_redis") else None

        async def _monkey_get_redis():
            return fake

        # 直接替换模块内引用的 get_redis 不方便（在函数内 import），
        # 用 monkeypatch 方式：把 _get_redis 重绑定。
        saver._get_redis = _monkey_get_redis
        return saver, fake

    async def test_put_and_get_tuple(self):
        saver, fake = await self._saver_with_fake_redis()
        state = AgentState(session_id="s1", user_id=1, user_message="退款")
        cfg_in = _config(thread_id="t1")
        cfg_out = await saver.aput(cfg_in, _checkpoint(cid="cp-1", state=state), {"source": "test"}, {})
        assert cfg_out["configurable"]["checkpoint_id"] == "cp-1"

        tup = await saver.aget_tuple(cfg_in)
        assert tup is not None
        assert tup.checkpoint["id"] == "cp-1"
        assert tup.checkpoint["channel_values"]["state"].user_message == "退款"
        assert tup.metadata["source"] == "test"

    async def test_pending_writes_roundtrip(self):
        saver, fake = await self._saver_with_fake_redis()
        cfg_out = await saver.aput(_config(thread_id="t2"), _checkpoint(cid="cp-2"), {}, {})
        # 模拟 interrupt 写入（channel=__interrupt__，value 为 Command 描述）
        writes = [("__interrupt__", {"type": "refund_confirmation", "order_id": "ORD1"})]
        await saver.aput_writes(cfg_out, writes, task_id="task-x")

        tup = await saver.aget_tuple(_config(thread_id="t2"))
        assert tup is not None
        assert tup.pending_writes
        channel, value = tup.pending_writes[0][1], tup.pending_writes[0][2]
        assert channel == "__interrupt__"
        assert value["order_id"] == "ORD1"

    async def test_multiple_checkpoints_take_latest(self):
        saver, fake = await self._saver_with_fake_redis()
        await saver.aput(_config(thread_id="t3"), _checkpoint(cid="cp-a"), {}, {})
        await saver.aput(_config(thread_id="t3"), _checkpoint(cid="cp-b"), {}, {})
        tup = await saver.aget_tuple(_config(thread_id="t3"))
        assert tup.checkpoint["id"] == "cp-b"

    async def test_cross_instance_restore(self):
        """模拟进程重启：新 saver 实例（同一 Redis）能读到旧 checkpoint。"""
        saver1, fake = await self._saver_with_fake_redis()
        await saver1.aput(_config(thread_id="t4"), _checkpoint(cid="cp-keep"), {}, {})

        saver2 = RedisCheckpointSaver()
        saver2._redis_ok = True

        async def _g():
            return fake
        saver2._get_redis = _g
        tup = await saver2.aget_tuple(_config(thread_id="t4"))
        assert tup is not None
        assert tup.checkpoint["id"] == "cp-keep"