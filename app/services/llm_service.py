"""
LLM 服务 - Phase 3 增强版
支持多模型切换、降级策略、重试机制、调用统计
"""
import json
import time
import logging
from typing import Optional, Tuple, List, Dict, Any, Literal
from datetime import datetime

from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config.config import settings
from app.services.llm_governance import BudgetManager, BudgetPolicy, CostEstimator, SensitiveDataRedactor, PromptInjectionScanner, ToolPolicy, build_default_registry

logger = logging.getLogger(__name__)


class LLMResponse(BaseModel):
    """Stable contract for model-generated customer-service responses."""

    reply: str = Field(default="", description="给用户的中文回复")
    intent: Literal[
        "query_order", "refund", "complaint", "technical",
        "promotion", "human", "general"
    ] = "general"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    citations: List[str] = Field(default_factory=list)
    need_human: bool = False
    next_action: Optional[str] = None

INTENT_KEYWORDS = {
    "query_order": ["订单", "物流", "发货", "快递", "查询", "单号"],
    "refund": ["退款", "退货", "退换", "售后", "拒收"],
    "complaint": ["投诉", "差评", "不满", "气愤", "骗子"],
    "technical": ["怎么用", "如何", "设置", "安装", "教程", "问题", "报错"],
    "promotion": ["优惠", "活动", "折扣", "券", "促销", "便宜"],
    "human": ["人工", "客服", "转人工", "找客服"],
}


class ModelConfig:
    """模型配置"""

    def __init__(
        self,
        name: str,
        api_base: str = "",
        api_key: str = "",
        model: str = "",
        is_local: bool = False,
        priority: int = 0,
    ):
        self.name = name
        self.api_base = api_base
        self.api_key = api_key
        self.model = model
        self.is_local = is_local
        self.priority = priority
        self.failure_count = 0
        self.last_failure_time: float = 0
        self.circuit_open: bool = False


class CircuitBreaker:
    """熔断器 - 防止故障模型被反复调用"""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 300,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.states: Dict[str, Dict[str, Any]] = {}

    def can_execute(self, model_name: str) -> bool:
        state = self.states.get(model_name, {})

        if not state.get("open", False):
            return True

        last_failure = state.get("last_failure", 0)
        if time.time() - last_failure > self.recovery_timeout:
            self.reset(model_name)
            return True

        return False

    def record_failure(self, model_name: str):
        state = self.states.setdefault(model_name, {"failures": 0, "open": False, "last_failure": 0})
        state["failures"] = state.get("failures", 0) + 1
        state["last_failure"] = time.time()

        if state["failures"] >= self.failure_threshold:
            state["open"] = True
            logger.warning(f"模型 {model_name} 熔断器打开")

    def record_success(self, model_name: str):
        self.reset(model_name)

    def reset(self, model_name: str):
        self.states[model_name] = {"failures": 0, "open": False, "last_failure": 0}


class LLMService:
    """LLM 服务 - 支持多模型降级"""

    MAX_RETRIES = 2
    RETRY_DELAY = 1.0
    TIMEOUT = 30.0

    def __init__(self):
        self.MAX_RETRIES = settings.LLM_MAX_RETRIES
        self.TIMEOUT = settings.LLM_REQUEST_TIMEOUT_SECONDS
        self.models: List[ModelConfig] = []
        self.current_model_index: int = 0
        self.circuit_breaker = CircuitBreaker()
        self._chat_models: Dict[str, ChatOpenAI] = {}
        self.call_stats: Dict[str, Dict[str, int]] = {}
        self.registry = build_default_registry(settings.LLM_PROMPT_VERSION, settings.LLM_MODEL_VERSION or settings.LLM_MODEL, settings.LLM_SCHEMA_VERSION, settings.LLM_ROUTE_VERSION)
        self.redactor = SensitiveDataRedactor()
        self.injection_scanner = PromptInjectionScanner()
        self.tool_policy = ToolPolicy()
        self.budget_manager = BudgetManager(BudgetPolicy(max_tokens=settings.LLM_MAX_TOKENS, max_cost_usd=settings.LLM_MAX_COST_USD, user_daily_tokens=settings.LLM_DAILY_USER_TOKEN_BUDGET, user_daily_cost_usd=settings.LLM_DAILY_USER_COST_USD, max_latency_seconds=settings.LLM_REQUEST_TIMEOUT_SECONDS), CostEstimator(settings.LLM_INPUT_PRICE_PER_1K_USD, settings.LLM_OUTPUT_PRICE_PER_1K_USD))
        self.last_call_metadata: Dict[str, Any] = {}

        self._init_models()

    def _init_models(self):
        primary = ModelConfig(
            name="primary",
            api_base=settings.LLM_API_BASE,
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
            is_local=False,
            priority=0,
        )
        self.models.append(primary)

        if hasattr(settings, "LLM_BACKUP_BASE") and settings.LLM_BACKUP_BASE:
            backup = ModelConfig(
                name="backup",
                api_base=settings.LLM_BACKUP_BASE,
                api_key=getattr(settings, "LLM_BACKUP_KEY", ""),
                model=getattr(settings, "LLM_BACKUP_MODEL", "gpt-4o-mini"),
                is_local=False,
                priority=1,
            )
            self.models.append(backup)

        local = ModelConfig(
            name="local_fallback",
            api_base="",
            api_key="",
            model="local_mock",
            is_local=True,
            priority=2,
        )
        self.models.append(local)

    async def close(self):
        self._chat_models.clear()

    def detect_intent(self, message: str) -> str:
        message_lower = message.lower()
        scores = {}
        for intent, keywords in INTENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw.lower() in message_lower)
            if score > 0:
                scores[intent] = score

        if scores:
            return max(scores, key=scores.get)
        return "general"

    async def chat(
        self,
        user_id: int,
        session_id: str,
        message: str,
        context: Optional[list] = None,
    ) -> Tuple[str, str, int]:
        started = time.perf_counter()
        intent = self.detect_intent(message)
        injection_patterns = self.injection_scanner.scan(message)
        input_tokens = max(1, len(message) // 4)
        budget = self.budget_manager.check(user_id, input_tokens, settings.LLM_MAX_TOKENS)
        self.last_call_metadata = {
            "prompt_version": self.registry.get("prompt").version,
            "model_version": self.registry.get("model").version,
            "schema_version": self.registry.get("schema").version,
            "route_version": self.registry.get("route").version,
            "input_summary": self.redactor.summary(message),
            "injection_detected": bool(injection_patterns),
            "injection_patterns": injection_patterns,
            "budget_allowed": budget.allowed,
            "budget_reason": budget.reason,
            "estimated_cost_usd": budget.estimated_cost_usd,
            "cost_basis": "configured_token_rates_estimate",
        }
        if injection_patterns or not budget.allowed:
            reason = "prompt_injection" if injection_patterns else budget.reason
            reply = "抱歉，我不能执行修改系统规则、泄露凭据或绕过权限的请求。请描述具体的业务问题。" if injection_patterns else "当前模型预算已用尽，我将使用规则模式继续为您服务。"
            tokens = max(1, len(reply) // 4)
            self._record_call_metadata(user_id, "rule_fallback", tokens, started, degraded=True, reason=reason)
            return reply, intent, tokens

        # 本地 profile 明确启用 Mock 时，不尝试访问外部模型，保证离线可复现。
        if settings.MOCK_LLM_ENABLED:
            model = next((m for m in self.models if m.is_local), None)
            if model:
                self._record_stats(model.name, True)
                reply = self._get_mock_reply(intent, message)
                self._record_call_metadata(user_id, model.name, len(message), started, degraded=True, reason="mock_mode")
                return reply, intent, len(message)

        for attempt in range(len(self.models)):
            model = self.models[attempt]

            if not self.circuit_breaker.can_execute(model.name):
                logger.info(f"模型 {model.name} 熔断器开启，跳过")
                continue

            try:
                if model.is_local:
                    reply = self._get_mock_reply(intent, message)
                    self.circuit_breaker.record_success(model.name)
                    self._record_stats(model.name, True)
                    return reply, intent, len(message)

                if model.api_key and model.api_key != "your-api-key-here":
                    reply, model_intent, token_count = await self._call_model_api(
                        model, user_id, session_id, message, context
                    )
                    self.circuit_breaker.record_success(model.name)
                    self._record_stats(model.name, True)
                    self._record_call_metadata(user_id, model.name, token_count, started)
                    return reply, model_intent or intent, token_count

            except Exception as e:
                logger.warning(f"模型 {model.name} 调用失败: {e}")
                self.circuit_breaker.record_failure(model.name)
                self._record_stats(model.name, False)

                if attempt < len(self.models) - 1:
                    logger.info(f"降级到下一个模型")
                    continue

        reply = self._get_mock_reply(intent, message)
        self._record_call_metadata(user_id, "rule_fallback", len(message), started, degraded=True, reason="provider_unavailable")
        return reply, intent, len(message)

    def _record_call_metadata(self, user_id: int, model_name: str, tokens: int, started: float, *, degraded: bool = False, reason: str | None = None) -> None:
        input_tokens = max(1, len(self.last_call_metadata.get("input_summary", "")) // 4)
        cost = self.budget_manager.estimator.estimate(input_tokens, tokens)
        self.budget_manager.consume(user_id, tokens, cost)
        self.last_call_metadata.update({"model": model_name, "route": "mock" if model_name == "local_fallback" else ("rule_fallback" if model_name == "rule_fallback" else "external"), "tokens": max(0, int(tokens)), "cost_usd": cost, "latency_ms": int((time.perf_counter() - started) * 1000), "degraded": degraded, "degraded_reason": reason})

    def get_last_call_metadata(self) -> Dict[str, Any]:
        return dict(self.last_call_metadata)

    async def _call_model_api(
        self,
        model: ModelConfig,
        user_id: int,
        session_id: str,
        message: str,
        context: Optional[list] = None,
    ) -> Tuple[str, str, int]:
        system_prompt = self._get_system_prompt()
        messages = [SystemMessage(content=system_prompt)]

        if context:
            for item in self.redactor.sanitize_messages(context[-10:]):
                role = item.get("role", "user")
                content = item.get("content", "")
                if role == "assistant":
                    messages.append(AIMessage(content=content))
                else:
                    messages.append(HumanMessage(content=content))

        messages.append(HumanMessage(content=self.redactor.redact(message)))

        chat_model = self._get_chat_model(model)
        structured_model = (
            chat_model.with_structured_output(LLMResponse)
            if hasattr(chat_model, "with_structured_output")
            else chat_model
        )
        response = await structured_model.ainvoke(
            messages,
            config={
                "run_name": "customer_service_chat",
                "tags": ["customer-service", model.name],
                "metadata": {"user_id": user_id, "session_id": session_id},
            },
        )

        if isinstance(response, LLMResponse):
            reply = response.reply
            response_intent = response.intent
            self.last_call_metadata["structured_output"] = response.model_dump()
        else:
            reply = self._message_content(response)
            response_intent = self.detect_intent(message)
            self.last_call_metadata["structured_output"] = {
                "reply": reply,
                "intent": response_intent,
                "confidence": 0.0,
                "citations": [],
                "need_human": False,
                "next_action": None,
            }
        token_count = self._message_token_count(response, fallback=len(message))

        return reply, response_intent, token_count

    def _get_chat_model(self, model: ModelConfig) -> ChatOpenAI:
        """Create one LangChain ChatOpenAI instance per configured model."""
        cached = self._chat_models.get(model.name)
        if cached is not None:
            return cached

        chat_model = ChatOpenAI(
            model=model.model,
            api_key=model.api_key,
            base_url=model.api_base,
            temperature=0.7,
            timeout=self.TIMEOUT,
            max_retries=0,
            max_completion_tokens=min(settings.LLM_MAX_TOKENS, self.budget_manager.policy.max_tokens),
            streaming=False,
        )
        self._chat_models[model.name] = chat_model
        return chat_model

    @staticmethod
    def _message_content(message: Any) -> str:
        content = getattr(message, "content", message)
        if isinstance(content, list):
            return "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return str(content or "")

    @staticmethod
    def _message_token_count(message: Any, fallback: int = 0) -> int:
        usage = getattr(message, "usage_metadata", None) or {}
        if usage:
            return int(usage.get("total_tokens", 0) or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)))
        metadata = getattr(message, "response_metadata", None) or {}
        token_usage = metadata.get("token_usage", metadata.get("usage", {})) or {}
        return int(token_usage.get("total_tokens", fallback) or fallback)

    def _get_system_prompt(self) -> str:
        return """你是一个专业的智能客服助手。你的职责是：
1. 友好、专业地回应用户问题
2. 准确理解用户意图，提供准确的答案
3. 对于订单查询、退换货等操作，引导用户提供必要信息
4. 当无法解决时，主动建议转接人工客服
5. 回答要简洁明了，避免冗长"""

    def _get_mock_reply(self, intent: str, message: str) -> str:
        replies = {
            "query_order": "好的，我来帮您查询订单信息。请提供您的订单号，我会为您查询最新的物流状态和订单详情。",
            "refund": "关于退换货的问题，我可以帮您处理。请问您的订单号是多少？另外，请说明退换货的原因，我会为您指引相应的流程。",
            "complaint": "非常抱歉给您带来不好的体验。我已经记录了您的反馈，会有专门的客服团队尽快与您联系处理。",
            "technical": "我来帮您解决这个问题。请详细描述一下您遇到的情况，包括具体的操作步骤和报错信息，这样我能更准确地为您提供解决方案。",
            "promotion": "目前我们有以下优惠活动：\n1. 新用户首单立减20元\n2. 满199减30\n3. VIP会员享受95折优惠\n您可以在结算时自动享受相应优惠。",
            "human": "好的，正在为您转接人工客服。预计等待时间约1-3分钟，请稍候...",
            "general": "感谢您的咨询。我已经收到您的消息，会尽快为您处理。如果您有订单相关的问题，可以提供订单号；如果是其他问题，请详细描述，我会尽力帮助您。",
        }
        return replies.get(intent, replies["general"])

    def _record_stats(self, model_name: str, success: bool):
        stats = self.call_stats.setdefault(model_name, {"success": 0, "failure": 0, "total": 0})
        stats["total"] += 1
        if success:
            stats["success"] += 1
        else:
            stats["failure"] += 1

    def get_stats(self) -> Dict[str, Any]:
        return {
            "models": [
                {
                    "name": m.name,
                    "is_local": m.is_local,
                    "failure_count": m.failure_count,
                    "circuit_open": self.circuit_breaker.states.get(m.name, {}).get("open", False),
                    "stats": self.call_stats.get(m.name, {"success": 0, "failure": 0, "total": 0}),
                }
                for m in self.models
            ],
            "current_model": self.current_model_index,
            "timestamp": datetime.now().isoformat(),
        }

    def reset_circuit(self, model_name: Optional[str] = None):
        if model_name:
            self.circuit_breaker.reset(model_name)
        else:
            for m in self.models:
                self.circuit_breaker.reset(m.name)
