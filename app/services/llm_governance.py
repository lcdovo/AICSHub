"""LLM 治理：版本、预算、脱敏和安全边界。

该模块不负责业务决策。它只在模型边界上执行可审计的策略，因而 Mock、规则和
外部供应商路径都可以复用同一套约束。
"""
from __future__ import annotations
from app.utils.utc_time import utc_now

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VersionedArtifact:
    kind: str
    name: str
    version: str
    content: str = ""
    schema: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ArtifactRegistry:
    """进程内 registry；生产可由配置中心/数据库替换，接口保持不变。"""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, VersionedArtifact]] = {}
        self._active: dict[str, str] = {}
        self._lock = threading.RLock()

    def register(self, artifact: VersionedArtifact, *, activate: bool = False) -> VersionedArtifact:
        with self._lock:
            self._items.setdefault(artifact.kind, {})[artifact.version] = artifact
            if activate or artifact.kind not in self._active:
                self._active[artifact.kind] = artifact.version
        return artifact

    def activate(self, kind: str, version: str) -> VersionedArtifact:
        with self._lock:
            if version not in self._items.get(kind, {}):
                raise KeyError(f"unknown {kind} version: {version}")
            self._active[kind] = version
            return self._items[kind][version]

    def get(self, kind: str, version: str | None = None) -> VersionedArtifact:
        with self._lock:
            selected = version or self._active.get(kind)
            if not selected or selected not in self._items.get(kind, {}):
                raise KeyError(f"unknown {kind} version: {selected}")
            return self._items[kind][selected]

    def list(self, kind: str | None = None) -> list[VersionedArtifact]:
        with self._lock:
            kinds = [kind] if kind else list(self._items)
            return [item for k in kinds for item in self._items.get(k, {}).values()]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active": dict(self._active),
                "versions": {k: sorted(v) for k, v in self._items.items()},
            }


# Explicit names make the registry easy to discover in dependency injection code.
PromptRegistry = ArtifactRegistry
ModelRegistry = ArtifactRegistry


@dataclass(frozen=True)
class BudgetPolicy:
    max_tokens: int = 1024
    max_cost_usd: float = 0.05
    max_latency_seconds: float = 30.0
    user_daily_tokens: int = 100_000
    user_daily_cost_usd: float = 5.0


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str | None = None
    estimated_tokens: int = 0
    estimated_cost_usd: float = 0.0


class CostEstimator:
    """按配置单价估算费用，明确是 estimate 而非供应商账单。"""

    def __init__(self, input_per_1k: float = 0.00015, output_per_1k: float = 0.00060) -> None:
        self.input_per_1k = max(0.0, float(input_per_1k))
        self.output_per_1k = max(0.0, float(output_per_1k))

    def estimate(self, input_tokens: int, output_tokens: int) -> float:
        return round(max(0, input_tokens) / 1000 * self.input_per_1k + max(0, output_tokens) / 1000 * self.output_per_1k, 8)


class BudgetManager:
    """用户日预算闸门。

    存储后端：Redis 优先（hash key ``budget:{user_id}:{day}``，字段 tokens/cost，
    多 worker 共享同一份日累计），Redis 不可用时降级进程内 dict（单 worker 语义
    不变）。同步 ``check``/``consume`` 走进程内路径，供测试与离线场景使用；
    请求热路径（LLMService.chat / stream_chat）使用 ``acheck``/``aconsume``。
    注意：预算基于估算 token，是软闸门而非账单系统，check 与 consume 之间的
    读改写竞态允许微小超额。
    """

    REDIS_PREFIX = "budget"
    # 48h 滑动过期：日桶由 key 内日期保证，TTL 只负责清理不再活跃的 key。
    REDIS_TTL_SECONDS = 172800

    def __init__(self, policy: BudgetPolicy | None = None, estimator: CostEstimator | None = None) -> None:
        self.policy = policy or BudgetPolicy()
        self.estimator = estimator or CostEstimator()
        self._usage: dict[tuple[str, str], dict[str, float]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _day() -> str:
        return utc_now().strftime("%Y-%m-%d")

    def _redis_key(self, user_id: int | str) -> str:
        return f"{self.REDIS_PREFIX}:{user_id}:{self._day()}"

    @staticmethod
    async def _get_redis():
        from app.utils.database import get_redis

        try:
            return await get_redis()
        except Exception:
            return None

    def _check_request_limits(self, input_tokens: int, requested_output_tokens: int | None) -> BudgetDecision | None:
        """单次请求级限额检查；通过返回 None，超限返回拒绝决策。"""
        requested = max(0, requested_output_tokens if requested_output_tokens is not None else self.policy.max_tokens)
        estimated = max(0, int(input_tokens)) + requested
        cost = self.estimator.estimate(input_tokens, requested)
        if requested > self.policy.max_tokens:
            return BudgetDecision(False, "request_output_token_budget_exceeded", estimated, cost)
        if cost > self.policy.max_cost_usd:
            return BudgetDecision(False, "request_cost_budget_exceeded", estimated, cost)
        return None

    def _check_daily(self, tokens_used: float, cost_used: float, estimated: int, cost: float) -> BudgetDecision:
        if tokens_used + estimated > self.policy.user_daily_tokens:
            return BudgetDecision(False, "user_daily_token_budget_exceeded", estimated, cost)
        if cost_used + cost > self.policy.user_daily_cost_usd:
            return BudgetDecision(False, "user_daily_cost_budget_exceeded", estimated, cost)
        return BudgetDecision(True, estimated_tokens=estimated, estimated_cost_usd=cost)

    def _estimate(self, input_tokens: int, requested_output_tokens: int | None) -> tuple[int, float]:
        """与 _check_request_limits 同口径的估算值（检查通过后复用）。"""
        requested = max(0, requested_output_tokens if requested_output_tokens is not None else self.policy.max_tokens)
        estimated = max(0, int(input_tokens)) + requested
        return estimated, self.estimator.estimate(input_tokens, requested)

    def check(self, user_id: int | str, input_tokens: int, requested_output_tokens: int | None = None) -> BudgetDecision:
        """进程内同步检查（测试/离线路径）。"""
        denied = self._check_request_limits(input_tokens, requested_output_tokens)
        if denied is not None:
            return denied
        estimated, cost = self._estimate(input_tokens, requested_output_tokens)
        with self._lock:
            usage = self._usage.get((str(user_id), self._day()), {"tokens": 0, "cost": 0.0})
        return self._check_daily(usage["tokens"], usage["cost"], estimated, cost)

    async def acheck(self, user_id: int | str, input_tokens: int, requested_output_tokens: int | None = None) -> BudgetDecision:
        """请求热路径检查：Redis 共享日累计优先，失败降级进程内。"""
        denied = self._check_request_limits(input_tokens, requested_output_tokens)
        if denied is not None:
            return denied
        estimated, cost = self._estimate(input_tokens, requested_output_tokens)
        redis = await self._get_redis()
        if redis is not None:
            try:
                raw = await redis.hgetall(self._redis_key(user_id))
                tokens_used = int((raw or {}).get("tokens", 0) or 0)
                cost_used = float((raw or {}).get("cost", 0.0) or 0.0)
                return self._check_daily(tokens_used, cost_used, estimated, cost)
            except Exception as exc:
                logger.warning("预算 Redis 读取失败，降级进程内: %s", exc)
        return self.check(user_id, input_tokens, requested_output_tokens)

    def consume(self, user_id: int | str, tokens: int, cost_usd: float) -> None:
        key = (str(user_id), self._day())
        with self._lock:
            usage = self._usage.setdefault(key, {"tokens": 0, "cost": 0.0})
            usage["tokens"] += max(0, int(tokens))
            usage["cost"] += max(0.0, float(cost_usd))

    async def aconsume(self, user_id: int | str, tokens: int, cost_usd: float) -> None:
        """请求热路径累计：Redis 多 worker 共享优先，失败降级进程内。"""
        redis = await self._get_redis()
        if redis is not None:
            try:
                key = self._redis_key(user_id)
                pipe = redis.pipeline()
                pipe.hincrby(key, "tokens", max(0, int(tokens)))
                pipe.hincrbyfloat(key, "cost", max(0.0, float(cost_usd)))
                pipe.expire(key, self.REDIS_TTL_SECONDS)
                await pipe.execute()
                return
            except Exception as exc:
                logger.warning("预算 Redis 写入失败，降级进程内: %s", exc)
        self.consume(user_id, tokens, cost_usd)

    def usage(self, user_id: int | str) -> dict[str, float | str]:
        with self._lock:
            value = dict(self._usage.get((str(user_id), self._day()), {"tokens": 0, "cost": 0.0}))
        value["date"] = self._day()
        return value


class SensitiveDataRedactor:
    """最小化发送给外部模型的信息，并允许安全审计验证原文未泄露。"""

    PATTERNS = (
        (re.compile(r'''(?i)(["'](?:password|passwd|token|access[_ -]?token|refresh[_ -]?token|api[_ -]?key|secret|authorization|address|phone|email|密码|口令|令牌|密钥|收货地址|地址|手机|电话|邮箱)["']\s*:\s*)(["'])(?:\\.|(?!\2)[^\\])*\2'''), r'\1\2[REDACTED]\2'),
        (re.compile(r"(?i)bearer\s+[A-Za-z0-9_.~+/-]+=*"), "Bearer [REDACTED]"),
        (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), "[TOKEN_REDACTED]"),
        (re.compile(r"(?i)(://)[^\s/@:]+:[^\s/@]+@"), r"\1[REDACTED]@"),
        (re.compile(r"(?i)(password|passwd|token|api[_ -]?key|secret|密码|口令|令牌|密钥)\s*[:=：]\s*[^\s,;，；。]+"), r"\1=[REDACTED]"),
        (re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)"), "[PHONE_REDACTED]"),
        (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL_REDACTED]"),
        (re.compile(r"(?i)(详细地址|收货地址|地址)\s*[:：]\s*[^,;，。\n]+"), r"\1：[ADDRESS_REDACTED]"),
    )
    UNICODE_PATTERNS = (
        (re.compile(r"(?i)(?:\u5bc6\u7801|\u4ee4\u724c|\u5bc6\u94a5|\u53e3\u4ee4)\s*[:=：]\s*[^\s,;，；。\n]+"), "[SECRET_REDACTED]"),
        (re.compile(r"(?:\u8be6\u7ec6\u5730\u5740|\u6536\u8d27\u5730\u5740|\u5730\u5740)\s*[:：]\s*[^,;，；。\n]+"), "[ADDRESS_REDACTED]"),
    )

    def redact(self, value: str) -> str:
        result = str(value or "")
        # 结构化模型输入必须按字段脱敏；文本正则不能可靠匹配带引号/Unicode转义的键。
        if result.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                pass
            except RecursionError:
                return "[REDACTED_UNPROCESSABLE_JSON]"
            else:
                if isinstance(parsed, (dict, list)):
                    try:
                        return json.dumps(self.redact_value(parsed), ensure_ascii=False)
                    except RecursionError:
                        return "[REDACTED_UNPROCESSABLE_JSON]"
        for pattern, replacement in self.PATTERNS:
            result = pattern.sub(replacement, result)
        for pattern, replacement in self.UNICODE_PATTERNS:
            result = pattern.sub(replacement, result)
        return result

    # Stable aliases used by integrations and security tests.
    redact_text = redact

    def redact_value(self, value):
        if isinstance(value, dict):
            return {key: ("[REDACTED]" if re.search(r"(?i)password|passwd|secret|access.?token|refresh.?token|^token$|authorization|api.?key|address|phone|email|密码|口令|令牌|密钥|地址|手机|电话|邮箱", str(key))
                          else self.redact_value(item)) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact_value(item) for item in value]
        return self.redact(value) if isinstance(value, str) else value

    def sanitize_messages(self, messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [{**dict(message), "content": self.redact(str(message.get("content", "")))} for message in messages]

    redact_messages = sanitize_messages

    def summary(self, value: str, limit: int = 240) -> str:
        clean = self.redact(value).replace("\n", " ").strip()
        return clean[:limit] + ("…" if len(clean) > limit else "")


class PromptInjectionScanner:
    PATTERNS = (
        r"(?i)(ignore|disregard|forget)\s+(all\s+)?previous\s+(instructions|messages)",
        r"(?i)(忽略|无视|忘记)(之前|以上|前面)(的)?(指令|提示|规则|prompt)",
        r"(?i)(system\s*prompt|developer\s+message|role\s*:\s*system)",
        r"(?i)(输出|泄露|显示).{0,12}(密钥|token|密码|系统提示)",
    )

    def scan(self, text: str) -> list[str]:
        patterns = list(self.PATTERNS) + [
            r"(?i)(?:show|reveal|display|leak).{0,20}(?:api\s*key|password|token|system\s*prompt)",
            r"(?i)(?:\u5ffd\u7565|\u65e0\u89c6|\u5fd8\u8bb0)(?:\u4e4b\u524d|\u4ee5\u4e0a|\u524d\u9762)(?:\u7684)?(?:\u6307\u4ee4|\u63d0\u793a|prompt)",
            r"(?i)(?:\u663e\u793a|\u8f93\u51fa|\u6cc4\u9732).{0,12}(?:\u5bc6\u94a5|\u4ee4\u724c|\u5bc6\u7801|\u7cfb\u7edf\u63d0\u793a)",
        ]
        return [pattern for pattern in patterns if re.search(pattern, text or "")]

    def is_safe(self, text: str) -> bool:
        return not self.scan(text)

    contains_prompt_injection = lambda self, text: bool(self.scan(text))


class ToolPolicy:
    """LLM 只能提出白名单工具；最终权限和副作用仍由代码校验。"""

    DEFAULT_ALLOWED = frozenset({"query_order", "apply_refund", "create_ticket", "hybrid_search", "handoff_human"})

    def __init__(self, allowed: set[str] | None = None) -> None:
        self.allowed = frozenset(allowed or self.DEFAULT_ALLOWED)

    def validate(self, tool_name: str, arguments: Mapping[str, Any] | None = None) -> tuple[bool, str | None]:
        if tool_name not in self.allowed:
            return False, "tool_not_allowlisted"
        if not isinstance(arguments or {}, Mapping):
            return False, "tool_arguments_invalid"
        return True, None


def build_default_registry(prompt_version: str = "v1", model_version: str = "gpt-4o-mini", schema_version: str = "response.v1", route_version: str = "rules-first.v1") -> ArtifactRegistry:
    registry = ArtifactRegistry()
    registry.register(VersionedArtifact("prompt", "customer_service", prompt_version, metadata={"safety": "code-enforced"}), activate=True)
    registry.register(VersionedArtifact("model", "chat", model_version), activate=True)
    registry.register(VersionedArtifact("schema", "response", schema_version, schema={"type": "object", "required": ["reply", "intent"]}), activate=True)
    registry.register(VersionedArtifact("route", "planner", route_version, metadata={"rules_first": True}), activate=True)
    return registry


def stable_gray_assignment(key: str, percentage: int) -> bool:
    """稳定灰度：同一用户在版本切换前后不会随机漂移。

    当前消费方：graph.py 的 Langfuse 会话粒度采样（同会话轨迹完整）。
    """
    bucket = int(hashlib.sha256(str(key).encode()).hexdigest()[:8], 16) % 100
    return bucket < max(0, min(100, int(percentage)))
