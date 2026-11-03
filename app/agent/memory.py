"""
多轮对话状态管理 - Phase 3 增强版
基于 Redis 的会话状态追踪
支持会话中断恢复、多设备同步、上下文压缩、智能摘要
Redis 不可用时降级为内存存储

职责边界：本管理器只负责多轮会话上下文（messages/摘要/分布式锁，key 前缀
session:*）；LangGraph interrupt 的执行 checkpoint 由 redis_checkpoint.py 的
RedisCheckpointSaver 负责（key 前缀 agent_ckpt:*）。两者 key 空间隔离，
各自的降级策略互相独立。

优化项：
- 摘要对象持久化到 Redis（进程重启不丢失）
- get_state 内置内存缓存（进程内复用）
- Token 数动态触发压缩（避免长对话 OOM）：由 ChatService 每轮调用 maybe_compress
- LLM 生成对话摘要（提升压缩后上下文质量）
- 分布式锁防止并发状态覆盖
- 压缩时保留槽位快照（长对话槽位不丢失）
"""
import asyncio
import contextlib
import uuid
from contextlib import asynccontextmanager
import json
import re
import time
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

from pydantic import BaseModel, Field

from app.agent.state import AgentState
from app.utils.database import get_redis

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM_PROMPT = (
    "你是客服对话摘要助手。将一段客服对话压缩为 200 字以内的简洁摘要，"
    "保留：1. 用户核心诉求与意图；2. 关键实体（订单号、金额等）；"
    "3. 已提供的解决方案或结论；4. 未解决的问题。"
    "只依据对话内容提炼，不臆造、不补充未提及的信息。"
)


class _SummaryResult(BaseModel):
    """对话摘要输出契约。"""

    summary_text: str = Field(default="", description="200 字以内的对话摘要")

SESSION_TTL_SECONDS = 86400
MAX_MESSAGES_BEFORE_COMPRESSION = 40
MIN_MESSAGES_AFTER_COMPRESSION = 10
SUMMARY_MAX_LENGTH = 500
# Token 估算阈值：超过此值也触发压缩（与消息数取先到者）
TOKEN_COMPRESSION_THRESHOLD = 3000
# 分布式锁超时：需大于单轮最长执行（长 LLM/流式回答可能超过 30s），
# 避免执行期间锁提前过期导致另一 worker 读旧状态覆盖本次写入。
LOCK_TIMEOUT_SECONDS = 90
# 锁重试间隔
LOCK_RETRY_INTERVAL = 0.5
# 锁最大重试次数
LOCK_MAX_RETRIES = 60


class SessionBusyError(RuntimeError):
    """A session already has an active turn."""


class SessionLeaseError(RuntimeError):
    """Shared session ownership could not be established or was lost."""


_RELEASE_LOCK = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
_SAVE_VERSIONED_STATE = """
local old = redis.call('get', KEYS[1])
if old then
 local ok, value = pcall(cjson.decode, old)
 if not ok or tonumber(value.session_version or -1) >= tonumber(ARGV[1]) then return 0 end
end
redis.call('setex', KEYS[1], ARGV[2], ARGV[3]); return 1
"""
_RENEW_LOCK = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"


class ConversationSummary:
    """对话摘要"""

    def __init__(self):
        self.summary_text: str = ""
        self.key_topics: List[str] = []
        self.entities: List[str] = []
        self.intent_history: List[str] = []
        self.message_count: int = 0
        self.created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary_text": self.summary_text,
            "key_topics": self.key_topics,
            "entities": self.entities,
            "intent_history": self.intent_history,
            "message_count": self.message_count,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationSummary":
        summary = cls()
        summary.summary_text = data.get("summary_text", "")
        summary.key_topics = data.get("key_topics", [])
        summary.entities = data.get("entities", [])
        summary.intent_history = data.get("intent_history", [])
        summary.message_count = data.get("message_count", 0)
        summary.created_at = data.get("created_at", "")
        return summary


class SessionManager:
    """会话状态管理器 - Phase 3 增强版"""

    def __init__(self):
        self._state_cache: Dict[str, Any] = {}  # session_id -> (AgentState, cached_at)
        self._redis_available: Optional[bool] = None
        self._summaries: Dict[str, ConversationSummary] = {}
        self._turn_locks: Dict[str, Any] = {}

    async def _is_redis_available(self) -> bool:
        if self._redis_available is not None:
            return self._redis_available

        redis = await get_redis()
        if redis is None:
            self._redis_available = False
            return False

        try:
            await redis.ping()
            self._redis_available = True
            return True
        except Exception:
            self._redis_available = False
            return False

    @asynccontextmanager
    async def session_lock(self, session_id: str):
        """Serialize hydration, execution and persistence, with owned Redis leases.

        Local serialization remains available without Redis. A shared lease failure
        after acquisition cancels the turn instead of allowing a stale state write.
        """
        entry = self._turn_locks.setdefault(session_id, {"lock": asyncio.Lock(), "users": 0})
        entry["users"] += 1
        local_acquired = False
        redis = None
        token = uuid.uuid4().hex
        key = f"session_lock:{session_id}"
        shared_acquired = False
        renewer = None
        lease_lost = False
        owner = asyncio.current_task()
        try:
            try:
                await asyncio.wait_for(entry["lock"].acquire(), timeout=LOCK_MAX_RETRIES * LOCK_RETRY_INTERVAL)
                local_acquired = True
            except asyncio.TimeoutError as exc:
                raise SessionBusyError("当前会话正在处理其他消息，请稍后重试") from exc
            if await self._is_redis_available():
                redis = await get_redis()
                try:
                    for _ in range(LOCK_MAX_RETRIES):
                        if await redis.set(key, token, nx=True, ex=LOCK_TIMEOUT_SECONDS):
                            shared_acquired = True
                            break
                        await asyncio.sleep(LOCK_RETRY_INTERVAL)
                except Exception as exc:
                    raise SessionLeaseError("会话锁服务暂时不可用，请稍后重试") from exc
                if not shared_acquired:
                    raise SessionBusyError("当前会话正在处理其他消息，请稍后重试")

                async def renew():
                    nonlocal lease_lost
                    while True:
                        await asyncio.sleep(LOCK_TIMEOUT_SECONDS / 3)
                        try:
                            renewed = await redis.eval(_RENEW_LOCK, 1, key, token, LOCK_TIMEOUT_SECONDS)
                            if renewed:
                                continue
                        except Exception:
                            pass
                        lease_lost = True
                        owner.cancel()
                        return

                renewer = asyncio.create_task(renew())

            async def assert_owner():
                if not shared_acquired:
                    return
                try:
                    owned = await redis.get(key) == token
                except Exception as exc:
                    raise SessionLeaseError("无法确认会话锁所有权，请重试") from exc
                if not owned:
                    raise SessionLeaseError("会话锁已失效，本轮处理中止，请重试")

            assert_owner.redis_lock_token = token if shared_acquired else None

            try:
                yield assert_owner
            except asyncio.CancelledError:
                if lease_lost:
                    raise SessionLeaseError("会话锁已失效，本轮处理中止，请重试") from None
                raise
        finally:
            if renewer is not None:
                renewer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renewer
            if shared_acquired:
                try:
                    await redis.eval(_RELEASE_LOCK, 1, key, token)
                except Exception:
                    logger.warning("会话锁释放失败，将等待租约到期")
            if local_acquired:
                entry["lock"].release()
            entry["users"] -= 1
            if not entry["users"]:
                self._turn_locks.pop(session_id, None)

    async def get_state(self, session_id: str) -> Optional[AgentState]:
        # Redis is authoritative while available: a local hit may predate a turn
        # completed by another worker, even immediately after acquiring its lock.
        if await self._is_redis_available():
            try:
                redis = await get_redis()
                data = await redis.get(f"session_state:v2:{session_id}")
                if data is None:
                    data = await redis.get(f"session:{session_id}")
                if data:
                    state = self._deserialize_state(json.loads(data))
                    if state.session_id != session_id:
                        raise ValueError("Snapshot session identity mismatch")
                    if len(self._state_cache)>=1024 and session_id not in self._state_cache:
                        self._state_cache.pop(next(iter(self._state_cache)))
                    self._state_cache[session_id] = (state.model_copy(deep=True), time.time())
                    return state
                self._state_cache.pop(session_id, None)
                return None
            except Exception as exc:
                raise SessionLeaseError("无法读取共享会话状态，请稍后重试") from exc
        cached = self._state_cache.get(session_id)
        if cached and time.time() - cached[1] < SESSION_TTL_SECONDS:
            return cached[0].model_copy(deep=True)
        self._state_cache.pop(session_id, None)
        return None

    async def initialize_state(self, session_id: str, user_id: int) -> None:
        """仅初始化不存在的共享快照；不覆盖轮次结果，不创建本地陈旧副本。"""
        if await self._is_redis_available():
            redis = await get_redis()
            state = AgentState(session_id=session_id, user_id=user_id, user_message="")
            await redis.set(
                f"session:{session_id}", json.dumps(self._serialize_state(state)),
                nx=True, ex=SESSION_TTL_SECONDS,
            )

    async def save_state(self, state: AgentState):
        old = self._state_cache.get(state.session_id)
        if state.session_version is not None and old and old[0].session_version is not None and old[0].session_version >= state.session_version:
            return
        if len(self._state_cache) >= 1024:
            expired = [key for key,value in self._state_cache.items() if time.time()-value[1] >= SESSION_TTL_SECONDS]
            for key in expired:
                self._state_cache.pop(key,None)
            if len(self._state_cache) >= 1024:
                self._state_cache.pop(next(iter(self._state_cache)))
        if state.session_version is not None and await self._is_redis_available():
            try:
                redis = await get_redis()
                saved = await redis.eval(_SAVE_VERSIONED_STATE,1,f"session_state:v2:{state.session_id}",
                    state.session_version,SESSION_TTL_SECONDS,json.dumps(self._serialize_state(state)))
                if saved:
                    self._state_cache[state.session_id]=(state.model_copy(deep=True),time.time())
                return
            except Exception as exc:
                raise SessionLeaseError("无法保存共享会话投影") from exc
        self._state_cache[state.session_id] = (state.model_copy(deep=True), time.time())

        if await self._is_redis_available():
            try:
                redis = await get_redis()
                await redis.setex(
                    f"session:{state.session_id}",
                    SESSION_TTL_SECONDS,
                    json.dumps(self._serialize_state(state)),
                )
            except Exception as e:
                raise SessionLeaseError("无法保存共享会话状态，请重试") from e

    async def maybe_compress(self, session_id: str) -> bool:
        """检查并按需压缩超长会话上下文，防止 messages 无限膨胀。

        由 ChatService 在每轮持久化后调用。触发条件 = 消息数或 Token 估算超阈值；
        压缩核心保留最近 MIN_MESSAGES_AFTER_COMPRESSION 条，其余由 LLM 摘要
        （不可用降级规则摘要）概括，并把关键槽位快照到摘要实体，最后持久化。
        返回是否触发了压缩。
        """
        state = await self.get_state(session_id)
        if not state or not self._should_compress(state.messages):
            return False

        compressed = await self._compress_context(state)
        if not compressed:
            return False

        summary = self._summarize_messages(state.messages[:-MIN_MESSAGES_AFTER_COMPRESSION])
        if len(self._summaries)>=1024 and session_id not in self._summaries:
            self._summaries.pop(next(iter(self._summaries)))
        self._summaries[session_id] = summary
        state.conversation_summary = summary.summary_text
        # 摘要持久化到 Redis，进程重启后不丢失
        await self._save_summary_to_redis(session_id, summary)
        # 压缩时保留槽位快照
        self._snapshot_collected_info(state, summary)
        await self.save_state(state)
        return True

    def _should_compress(self, messages: List[Dict[str, Any]]) -> bool:
        """判断是否需要压缩：消息数或 Token 估算超阈值即触发（先到者优先）。

        仅当消息数超过保留下限时才考虑压缩，避免刚压缩完又立刻触发。
        """
        if len(messages) <= MIN_MESSAGES_AFTER_COMPRESSION:
            return False
        return len(messages) > MAX_MESSAGES_BEFORE_COMPRESSION or self._needs_token_compression(messages)

    def _estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """粗略估算消息列表的 token 数：中文约1.5 token/字，英文约1 token/词。"""
        total_chars = sum(len(m.get("content", "")) for m in messages)
        return int(total_chars * 1.5)

    def _needs_token_compression(self, messages: List[Dict[str, Any]]) -> bool:
        """基于 token 估算判断是否需要压缩。"""
        return self._estimate_tokens(messages) > TOKEN_COMPRESSION_THRESHOLD

    async def _compress_context(self, state: AgentState) -> bool:
        if len(state.messages) <= MIN_MESSAGES_AFTER_COMPRESSION:
            return False

        recent = state.messages[-MIN_MESSAGES_AFTER_COMPRESSION:]
        older = state.messages[:-MIN_MESSAGES_AFTER_COMPRESSION]

        # 优先使用 LLM 生成摘要，降级到规则摘要
        summary = await self._generate_llm_summary(older)
        if not summary:
            summary = self._generate_summary(older)
        if not summary:
            return False

        summary_msg = {
            "role": "system",
            "content": f"[对话摘要] {summary}",
            "type": "summary",
            "message_count": len(older),
            "generated_at": datetime.now().isoformat(timespec="milliseconds"),
        }

        state.messages = [summary_msg] + recent
        logger.info(
            f"压缩会话上下文: {len(older)} 条消息压缩为摘要, 保留 {len(recent)} 条"
        )
        return True

    async def _generate_llm_summary(self, messages: List[Dict[str, Any]]) -> str:
        """调用 LLM 生成对话摘要，失败时返回空字符串降级到规则摘要。"""
        try:
            from app.services.llm_service import llm_service

            # 构建对话文本
            dialog_parts = []
            for msg in messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if role == "system" and msg.get("type") == "summary":
                    dialog_parts.append(f"[已有摘要] {content}")
                else:
                    dialog_parts.append(f"{role}: {content}")

            dialog_text = "\n".join(dialog_parts[-20:])  # 最多取20条避免过长

            # 走独立摘要 system prompt + 结构化契约，避免复用"客服答复"链路
            # （原实现调用 chat()，模型以客服身份执行摘要任务且输出被硬塞进 reply）。
            result = await llm_service.run_structured(
                SUMMARY_SYSTEM_PROMPT,
                dialog_text,
                _SummaryResult,
                tags=["conversation_summary"],
            )
            summary = result.summary_text.strip() if result else ""
            if summary and len(summary) > 10:
                return summary[:SUMMARY_MAX_LENGTH]
        except Exception as e:
            logger.warning(f"LLM 生成摘要失败，降级到规则摘要: {e}")

        return ""

    def _generate_summary(self, messages: List[Dict[str, Any]]) -> str:
        user_messages = [m for m in messages if m.get("role") == "user"]
        assistant_messages = [m for m in messages if m.get("role") == "assistant"]

        topics = []
        entities = []

        for msg in user_messages:
            content = msg.get("content", "")
            if content:
                topics.append(content[:30])

                order_ids = re.findall(r'ORD\d{6,}', content.upper())
                entities.extend(order_ids)

        for msg in assistant_messages:
            content = msg.get("content", "")
            if content and any(
                kw in content
                for kw in ["订单", "退款", "工单", "物流", "状态", "金额"]
            ):
                entities.append(content[:50])

        if not topics:
            return ""

        summary_parts = []
        for i, topic in enumerate(topics[-5:]):
            summary_parts.append(f"{i + 1}. 用户询问: {topic}")

        entity_str = ", ".join(set(entities[:5]))
        if entity_str:
            summary_parts.append(f"涉及实体: {entity_str}")

        return " | ".join(summary_parts)[:SUMMARY_MAX_LENGTH]

    def _summarize_messages(self, messages: List[Dict[str, Any]]) -> ConversationSummary:
        summary = ConversationSummary()
        summary.message_count = len(messages)
        summary.created_at = datetime.now().isoformat(timespec="milliseconds")

        user_messages = [m for m in messages if m.get("role") == "user"]
        assistant_messages = [m for m in messages if m.get("role") == "assistant"]

        topics = []
        for msg in user_messages:
            content = msg.get("content", "")
            if content:
                topics.append(content[:30])

        summary.key_topics = list(set(topics))[:10]

        entities = []
        for msg in messages:
            content = msg.get("content", "")
            order_ids = re.findall(r'ORD\d{6,}', content.upper())
            entities.extend(order_ids)

        summary.entities = list(set(entities))[:10]

        summary.intent_history = [
            m.get("intent", "unknown")
            for m in assistant_messages
            if m.get("intent")
        ]

        summary.summary_text = self._generate_summary(messages)

        return summary

    def _snapshot_collected_info(self, state: AgentState, summary: ConversationSummary):
        """压缩时将关键槽位信息快照保存到摘要的 entities 字段，作为冗余备份。"""
        key_slots = ["order_id", "refund_reason", "complaint_type", "tracking_number"]
        for key in key_slots:
            value = state.collected_info.get(key)
            if value and str(value) not in summary.entities:
                summary.entities.append(f"{key}={value}")

    async def _save_summary_to_redis(self, session_id: str, summary: ConversationSummary):
        """将摘要对象持久化到 Redis，进程重启后可恢复。"""
        if not await self._is_redis_available():
            return
        try:
            redis = await get_redis()
            await redis.setex(
                f"session_summary:{session_id}",
                SESSION_TTL_SECONDS,
                json.dumps(summary.to_dict()),
            )
        except Exception as e:
            logger.warning(f"Redis 保存摘要失败: {e}")

    async def get_summary(self, session_id: str) -> Optional[ConversationSummary]:
        """获取摘要：内存缓存 → Redis → state.conversation_summary。"""
        # 1. 内存缓存
        if not await self._is_redis_available() and session_id in self._summaries:
            return self._summaries[session_id]

        # 2. Redis 持久化摘要
        if await self._is_redis_available():
            try:
                redis = await get_redis()
                data = await redis.get(f"session_summary:{session_id}")
                if data:
                    summary = ConversationSummary.from_dict(json.loads(data))
                    if len(self._summaries)>=1024: self._summaries.pop(next(iter(self._summaries)))
                    self._summaries[session_id] = summary
                    return summary
            except Exception as e:
                logger.warning(f"Redis 读取摘要失败: {e}")

        # 3. 从 state.conversation_summary 降级
        state = await self.get_state(session_id)
        if state and state.conversation_summary:
            summary = ConversationSummary()
            summary.summary_text = state.conversation_summary
            summary.message_count = len(state.messages)
            return summary

        return None

    def _serialize_state(self, state: AgentState) -> Dict[str, Any]:
        if state.session_version is not None:
            clean = state.for_next_turn(state.user_id, "")
            return {"schema_version":2,"session_version":state.session_version,"session_id":state.session_id,
                    "user_id":state.user_id,"messages":state.messages[-20:],"collected_info":clean.collected_info,
                    "pending_confirmations":clean.pending_confirmations,"missing_slots":state.missing_slots,
                    "needs_clarification":state.needs_clarification,"conversation_summary":state.conversation_summary}
        return {
            "schema_version": 1,
            "session_id": state.session_id,
            "user_id": state.user_id,
            "user_message": state.user_message,
            "detected_intent": state.detected_intent,
            "intent_confidence": state.intent_confidence,
            "current_node": state.current_node,
            "rewritten_query": state.rewritten_query,
            "query_rewrite_applied": state.query_rewrite_applied,
            "query_rewrite_reason": state.query_rewrite_reason,
            "messages": state.messages,
            "collected_info": state.collected_info,
            "pending_confirmations": state.pending_confirmations,
            "missing_slots": state.missing_slots,
            "needs_clarification": state.needs_clarification,
            "reply": state.reply,
            "need_human": state.need_human,
            "user_emotion": state.user_emotion,
            "total_tokens": state.total_tokens,
            "conversation_summary": state.conversation_summary,
            "created_at": datetime.now().isoformat(timespec="milliseconds"),
        }

    def _deserialize_state(self, data: Dict[str, Any]) -> AgentState:
        # 旧无version对象兼容读取；整数元数据/未知schema不可当作空历史转换。
        if (not isinstance(data, dict) or type(data.get("schema_version", 1)) is not int
                or data.get("schema_version", 1) not in (1,2)):
            raise ValueError("Unsupported session snapshot schema")
        if (not isinstance(data.get("session_id"), str) or not data["session_id"]
                or type(data.get("user_id")) is not int or data["user_id"] <= 0):
            raise ValueError("Invalid session snapshot identity")
        if data.get("schema_version")==2 and (type(data.get("session_version")) is not int or data["session_version"]<0):
            raise ValueError("Invalid session version")
        return AgentState(
            session_version=data.get("session_version"),
            session_id=data.get("session_id", ""),
            user_id=data.get("user_id", 0),
            user_message=data.get("user_message", ""),
            detected_intent=data.get("detected_intent", "general"),
            intent_confidence=data.get("intent_confidence", 0.0),
            current_node=data.get("current_node", "start"),
            rewritten_query=data.get("rewritten_query"),
            query_rewrite_applied=data.get("query_rewrite_applied", False),
            query_rewrite_reason=data.get("query_rewrite_reason"),
            messages=data.get("messages", []),
            collected_info=data.get("collected_info", {}),
            pending_confirmations=data.get("pending_confirmations", []),
            missing_slots=data.get("missing_slots", []),
            needs_clarification=data.get("needs_clarification", False),
            reply=data.get("reply", ""),
            need_human=data.get("need_human", False),
            user_emotion=data.get("user_emotion", "neutral"),
            total_tokens=data.get("total_tokens", 0),
            conversation_summary=data.get("conversation_summary"),
        )


session_manager = SessionManager()
