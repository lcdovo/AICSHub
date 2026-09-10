"""
多轮对话状态管理 - Phase 3 增强版
基于 Redis 的会话状态追踪
支持会话中断恢复、多设备同步、上下文压缩、智能摘要
Redis 不可用时降级为内存存储

优化项：
- 摘要对象持久化到 Redis（进程重启不丢失）
- get_messages 增加数据库回退（保证最终一致性）
- Token 数动态触发压缩（避免长对话 OOM）
- LLM 生成对话摘要（提升压缩后上下文质量）
- 分布式锁防止并发状态覆盖
- 压缩时保留槽位快照（长对话槽位不丢失）
"""
import asyncio
import json
import re
import time
import uuid
import logging
from typing import Any, Dict, List, Optional, Tuple
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
# 分布式锁超时
LOCK_TIMEOUT_SECONDS = 30
# 锁重试间隔
LOCK_RETRY_INTERVAL = 0.5
# 锁最大重试次数
LOCK_MAX_RETRIES = 60


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
        self._state_cache: Dict[str, AgentState] = {}
        self._redis_available: Optional[bool] = None
        self._summaries: Dict[str, ConversationSummary] = {}

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

    async def acquire_lock(self, session_id: str) -> bool:
        """获取会话级分布式锁，防止并发请求状态覆盖。"""
        if not await self._is_redis_available():
            return True  # Redis 不可用时不加锁，直接放行

        redis = await get_redis()
        lock_key = f"session_lock:{session_id}"
        for _ in range(LOCK_MAX_RETRIES):
            acquired = await redis.set(lock_key, "1", nx=True, ex=LOCK_TIMEOUT_SECONDS)
            if acquired:
                return True
            await asyncio.sleep(LOCK_RETRY_INTERVAL)

        logger.warning(f"获取会话锁超时: {session_id}")
        return False

    async def release_lock(self, session_id: str):
        """释放会话级分布式锁。"""
        if not await self._is_redis_available():
            return
        try:
            redis = await get_redis()
            await redis.delete(f"session_lock:{session_id}")
        except Exception as e:
            logger.warning(f"释放会话锁失败: {e}")

    async def create_session(
        self, user_id: int, session_id: Optional[str] = None
    ) -> str:
        if not session_id:
            session_id = str(uuid.uuid4())

        state = AgentState(
            session_id=session_id,
            user_id=user_id,
            user_message="",
        )

        self._state_cache[session_id] = state

        if await self._is_redis_available():
            try:
                redis = await get_redis()
                await redis.setex(
                    f"session:{session_id}",
                    SESSION_TTL_SECONDS,
                    json.dumps(self._serialize_state(state)),
                )
                user_sessions_key = f"user_sessions:{user_id}"
                await redis.sadd(user_sessions_key, session_id)
                await redis.expire(user_sessions_key, SESSION_TTL_SECONDS)
            except Exception as e:
                logger.warning(f"Redis 存储失败: {e}")

        return session_id

    async def get_state(self, session_id: str) -> Optional[AgentState]:
        if session_id in self._state_cache:
            return self._state_cache[session_id]

        if await self._is_redis_available():
            try:
                redis = await get_redis()
                data = await redis.get(f"session:{session_id}")
                if data:
                    state = self._deserialize_state(json.loads(data))
                    self._state_cache[session_id] = state
                    return state
            except Exception as e:
                logger.warning(f"Redis 读取失败: {e}")

        return None

    async def save_state(self, state: AgentState):
        self._state_cache[state.session_id] = state

        if await self._is_redis_available():
            try:
                redis = await get_redis()
                await redis.setex(
                    f"session:{state.session_id}",
                    SESSION_TTL_SECONDS,
                    json.dumps(self._serialize_state(state)),
                )
            except Exception as e:
                logger.warning(f"Redis 保存失败: {e}")

    async def append_message(self, session_id: str, message: Dict[str, Any]):
        state = await self.get_state(session_id)
        if not state:
            return

        state.messages.append(message)

        if self._should_compress(state.messages):
            compressed = await self._compress_context(state)
            if compressed:
                summary = self._summarize_messages(state.messages[:-MIN_MESSAGES_AFTER_COMPRESSION])
                self._summaries[session_id] = summary
                state.conversation_summary = summary.summary_text
                # 摘要持久化到 Redis，进程重启后不丢失
                await self._save_summary_to_redis(session_id, summary)
                # 压缩时保留槽位快照
                self._snapshot_collected_info(state, summary)

        await self.save_state(state)

    def _should_compress(self, messages: List[Dict[str, Any]]) -> bool:
        """判断是否需要压缩：消息数或 Token 估算超过阈值时触发。"""
        if len(messages) <= MAX_MESSAGES_BEFORE_COMPRESSION:
            return False
        # 消息数超阈值直接压缩
        if len(messages) > MAX_MESSAGES_BEFORE_COMPRESSION:
            return True
        return False

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

    async def get_messages(
        self, session_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """获取会话消息，Redis/内存未命中时从数据库回退。"""
        state = await self.get_state(session_id)
        if state and state.messages:
            return state.messages[-limit:]

        # 数据库回退：保证最终一致性
        try:
            from app.utils.database import async_session
            from app.models.models import Message
            from sqlalchemy import select

            async with async_session() as db:
                rows = await db.execute(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.created_at)
                    .limit(limit)
                )
                messages = []
                for row in rows.scalars():
                    messages.append({
                        "role": row.role.value if hasattr(row.role, 'value') else str(row.role),
                        "content": row.content,
                        "timestamp": row.created_at.isoformat() if row.created_at else "",
                    })
                if messages:
                    logger.info(f"从数据库回退加载 {len(messages)} 条消息: {session_id}")
                return messages
        except Exception as e:
            logger.warning(f"数据库回退读取消息失败: {e}")
            return []

    async def get_summary(self, session_id: str) -> Optional[ConversationSummary]:
        """获取摘要：内存缓存 → Redis → state.conversation_summary。"""
        # 1. 内存缓存
        if session_id in self._summaries:
            return self._summaries[session_id]

        # 2. Redis 持久化摘要
        if await self._is_redis_available():
            try:
                redis = await get_redis()
                data = await redis.get(f"session_summary:{session_id}")
                if data:
                    summary = ConversationSummary.from_dict(json.loads(data))
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

    async def close_session(self, session_id: str):
        state = await self.get_state(session_id)
        if state:
            state.current_node = "end"
            await self.save_state(state)

        if await self._is_redis_available():
            try:
                redis = await get_redis()
                await redis.delete(f"session:{session_id}")
                await redis.delete(f"session_summary:{session_id}")
            except Exception as e:
                logger.warning(f"Redis 删除失败: {e}")

        if session_id in self._state_cache:
            del self._state_cache[session_id]
        if session_id in self._summaries:
            del self._summaries[session_id]

    async def get_user_sessions(
        self, user_id: int, limit: int = 10
    ) -> List[str]:
        if await self._is_redis_available():
            try:
                redis = await get_redis()
                user_sessions_key = f"user_sessions:{user_id}"
                session_ids = await redis.smembers(user_sessions_key)

                active_sessions = []
                for sid in session_ids:
                    state = await self.get_state(sid)
                    if state and state.current_node != "end":
                        active_sessions.append(sid)
                return active_sessions[:limit]
            except Exception as e:
                logger.warning(f"Redis 读取用户会话失败: {e}")

        return [
            sid
            for sid, state in self._state_cache.items()
            if state.user_id == user_id and state.current_node != "end"
        ][:limit]

    def get_session_stats(self, session_id: str) -> Dict[str, Any]:
        state = self._state_cache.get(session_id)
        if not state:
            return {"exists": False}

        summary = self._summaries.get(session_id)

        return {
            "exists": True,
            "message_count": len(state.messages),
            "has_summary": state.conversation_summary is not None,
            "summary_preview": (
                state.conversation_summary[:100]
                if state.conversation_summary
                else ""
            ),
            "current_intent": state.detected_intent,
            "last_node": state.current_node,
            "summaries_count": len(self._summaries),
            "total_cached_sessions": len(self._state_cache),
        }

    def _serialize_state(self, state: AgentState) -> Dict[str, Any]:
        return {
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
            "reply": state.reply,
            "need_human": state.need_human,
            "user_emotion": state.user_emotion,
            "total_tokens": state.total_tokens,
            "conversation_summary": state.conversation_summary,
            "created_at": datetime.now().isoformat(timespec="milliseconds"),
        }

    def _deserialize_state(self, data: Dict[str, Any]) -> AgentState:
        return AgentState(
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
            reply=data.get("reply", ""),
            need_human=data.get("need_human", False),
            user_emotion=data.get("user_emotion", "neutral"),
            total_tokens=data.get("total_tokens", 0),
            conversation_summary=data.get("conversation_summary"),
        )


session_manager = SessionManager()
