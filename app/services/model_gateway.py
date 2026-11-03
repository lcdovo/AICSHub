"""SQL模型网关：原子预留/结算、跨实例槽、每次尝试可审计，Redis不可用不绕过。"""

from __future__ import annotations
from contextvars import ContextVar
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal
import asyncio, hashlib, json, time, uuid
from sqlalchemy import select, update, or_
from app.config.config import settings
from app.models.models import ModelBudgetDay, ModelCallRecord, ModelConcurrencySlot

CALL_CONTEXT = ContextVar("model_call_context", default=None)


@asynccontextmanager
async def inherited_model_context(user_id: int, scope: str, *, route_version=None):
    """可信应用入口传入主体，嵌套调用继承同一Run，禁止串用其他用户账本。"""
    if not settings.LLM_GATEWAY_ENABLED:
        yield
        return
    if type(user_id) is not int or user_id <= 0:
        raise RuntimeError("trusted_model_user_required")
    current = CALL_CONTEXT.get()
    if current is not None:
        if current["user_id"] != user_id:
            raise RuntimeError("model_context_user_mismatch")
        yield
        return
    async with model_context(user_id, scope + ":" + uuid.uuid4().hex, route_version=route_version):
        yield


@asynccontextmanager
async def model_context(user_id: int, run_id: str, *, factory=None, route_version=None):
    token = CALL_CONTEXT.set(
        {
            "user_id": user_id,
            "run_id": run_id,
            "factory": factory,
            "route_version": route_version or settings.LLM_ROUTE_VERSION,
            "deadline": time.monotonic() + settings.LLM_REQUEST_TIMEOUT_SECONDS,
            "attempts": 0,
        }
    )
    try:
        yield
    finally:
        CALL_CONTEXT.reset(token)


def estimate_tokens(messages):
    # 完整消息+schema另加预算，采用UTF-8字节上界，不以脱敏摘要或len/4冒充真实usage。
    return max(
        1,
        len(
            json.dumps(
                [getattr(x, "content", str(x)) for x in messages], ensure_ascii=False
            ).encode()
        ),
    )


class ModelGateway:
    async def reserve(
        self, model, messages, purpose, output_limit, schema_version, *, extra_input_tokens=0
    ):
        context = CALL_CONTEXT.get()
        if context is None:
            raise RuntimeError("model_context_required")
        # 同一Run的并行子任务共享ContextVar中的字典。费用检查到提交必须串行，
        # SQL日预算负责跨Run/实例协调，不能替代这里的整轮费用上限。
        lock = context.setdefault("reservation_lock", asyncio.Lock())
        async with lock:
            return await self._reserve(
                model, messages, purpose, output_limit, schema_version,
                extra_input_tokens=extra_input_tokens,
            )

    async def _reserve(
        self,
        model,
        messages,
        purpose,
        output_limit,
        schema_version,
        *,
        extra_input_tokens=0,
    ):
        context = CALL_CONTEXT.get()
        if context is None:
            raise RuntimeError("model_context_required")
        if context["attempts"] >= 6 or time.monotonic() >= context["deadline"]:
            raise RuntimeError("run_model_budget_exhausted")
        context["attempts"] += 1
        from app.utils.database import async_session

        factory = context.get("factory") or async_session
        inp = estimate_tokens(messages) + extra_input_tokens
        tokens = inp + output_limit
        cost = Decimal(
            str(
                inp * settings.LLM_INPUT_PRICE_PER_1K_USD / 1000
                + output_limit * settings.LLM_OUTPUT_PRICE_PER_1K_USD / 1000
            )
        )
        if output_limit > settings.LLM_MAX_TOKENS or cost + Decimal(
            str(context.get("reserved_cost", 0))
        ) > Decimal(str(settings.LLM_MAX_COST_USD)):
            raise RuntimeError("request_model_budget_exceeded")
        now = datetime.utcnow()
        key = str(context["user_id"]) + ":" + now.strftime("%Y-%m-%d")
        cid = str(uuid.uuid4())
        async with factory() as db:
            dialect = db.bind.dialect.name
            if dialect == "mysql":
                from sqlalchemy.dialects.mysql import insert

                query = (
                    insert(ModelBudgetDay)
                    .values(key=key, tokens=0, cost=0)
                    .on_duplicate_key_update(key=key)
                )
            else:
                from sqlalchemy.dialects.sqlite import insert

                query = (
                    insert(ModelBudgetDay)
                    .values(key=key, tokens=0, cost=0)
                    .on_conflict_do_nothing(index_elements=["key"])
                )
            await db.execute(query)
            updated = await db.execute(
                update(ModelBudgetDay)
                .where(
                    ModelBudgetDay.key == key,
                    ModelBudgetDay.tokens + tokens
                    <= settings.LLM_DAILY_USER_TOKEN_BUDGET,
                    ModelBudgetDay.cost + cost
                    <= Decimal(str(settings.LLM_DAILY_USER_COST_USD)),
                )
                .values(
                    tokens=ModelBudgetDay.tokens + tokens,
                    cost=ModelBudgetDay.cost + cost,
                )
            )
            if updated.rowcount != 1:
                raise RuntimeError("daily_model_budget_exhausted")
            slot = None
            for index in range(1, settings.LLM_MAX_CONCURRENCY + 1):
                if dialect == "mysql":
                    from sqlalchemy.dialects.mysql import insert

                    init = (
                        insert(ModelConcurrencySlot)
                        .values(id=index)
                        .on_duplicate_key_update(id=index)
                    )
                else:
                    from sqlalchemy.dialects.sqlite import insert

                    init = (
                        insert(ModelConcurrencySlot)
                        .values(id=index)
                        .on_conflict_do_nothing(index_elements=["id"])
                    )
                await db.execute(init)
                prior = await db.get(
                    ModelConcurrencySlot, index, populate_existing=True
                )
                if (
                    prior
                    and prior.call_id
                    and prior.expires_at
                    and prior.expires_at <= now
                ):
                    await db.execute(
                        update(ModelCallRecord)
                        .where(
                            ModelCallRecord.id == prior.call_id,
                            ModelCallRecord.status == "reserved",
                        )
                        .values(
                            status="unknown",
                            usage_basis="unknown_reserved",
                            error_code="worker_interrupted",
                        )
                    )
                claimed = await db.execute(
                    update(ModelConcurrencySlot)
                    .where(
                        ModelConcurrencySlot.id == index,
                        or_(
                            ModelConcurrencySlot.call_id.is_(None),
                            ModelConcurrencySlot.expires_at <= now,
                        ),
                    )
                    .values(call_id=cid, expires_at=now + timedelta(seconds=60))
                )
                if claimed.rowcount == 1:
                    slot = index
                    break
            if slot is None:
                raise RuntimeError("model_concurrency_exhausted")
            db.add(
                ModelCallRecord(
                    id=cid,
                    user_id=context["user_id"],
                    run_id=context["run_id"],
                    purpose=purpose,
                    provider=model.name,
                    model=model.model,
                    model_version=settings.LLM_MODEL_VERSION,
                    price_snapshot={
                        "input_per_1k_usd": settings.LLM_INPUT_PRICE_PER_1K_USD,
                        "output_per_1k_usd": settings.LLM_OUTPUT_PRICE_PER_1K_USD,
                        "verified": settings.LLM_PRICE_VERIFIED,
                    },
                    route_version=context["route_version"],
                    schema_version=schema_version,
                    prompt_version=settings.LLM_PROMPT_VERSION,
                    status="reserved",
                    reserved_tokens=tokens,
                    reserved_cost=cost,
                    expires_at=now + timedelta(seconds=60),
                    request_hash=hashlib.sha256(
                        json.dumps(
                            [getattr(x, "content", str(x)) for x in messages],
                            ensure_ascii=False,
                        ).encode()
                    ).hexdigest(),
                )
            )
            await db.commit()
        context["reserved_cost"] = context.get("reserved_cost", 0) + float(cost)
        return {
            "id": cid,
            "factory": factory,
            "day": key,
            "slot": slot,
            "input_estimate": inp,
            "output_limit": output_limit,
            "cost": cost,
            "tokens": tokens,
            "started": time.monotonic(),
            "input_rate": settings.LLM_INPUT_PRICE_PER_1K_USD,
            "output_rate": settings.LLM_OUTPUT_PRICE_PER_1K_USD,
            "deadline": context["deadline"],
        }

    async def settle(self, ticket, response=None, *, error=None, dispatched=True):
        usage = getattr(response, "usage_metadata", None) or {}
        raw = getattr(response, "response_metadata", {}) or {}
        usage = usage or raw.get("token_usage", raw.get("usage", {})) or {}
        inp = usage.get("input_tokens", usage.get("prompt_tokens"))
        out = usage.get("output_tokens", usage.get("completion_tokens"))
        actual = type(inp) is int and type(out) is int and inp >= 0 and out >= 0
        async with ticket["factory"]() as db:
            call = await db.scalar(
                select(ModelCallRecord)
                .where(ModelCallRecord.id == ticket["id"])
                .with_for_update()
            )
            if call is None or call.status != "reserved":
                return
            if not dispatched:
                # 仅调用方确知尚未进入供应商适配器时退还预留；已发出/未知不走此分支。
                call.input_tokens, call.output_tokens, call.cost = 0, 0, Decimal(0)
                call.usage_basis = "not_sent"
                await db.execute(
                    update(ModelBudgetDay)
                    .where(ModelBudgetDay.key == ticket["day"])
                    .values(tokens=ModelBudgetDay.tokens - ticket["tokens"],
                            cost=ModelBudgetDay.cost - ticket["cost"])
                )
            elif actual:
                cost = Decimal(
                    str(
                        inp * ticket["input_rate"] / 1000
                        + out * ticket["output_rate"] / 1000
                    )
                )
                call.input_tokens, call.output_tokens, call.cost = inp, out, cost
                call.usage_basis = (
                    "provider_usage"
                    if (call.price_snapshot or {}).get("verified", False)
                    else "provider_tokens_est_price"
                )
                await db.execute(
                    update(ModelBudgetDay)
                    .where(ModelBudgetDay.key == ticket["day"])
                    .values(
                        tokens=ModelBudgetDay.tokens - ticket["tokens"] + inp + out,
                        cost=ModelBudgetDay.cost - ticket["cost"] + cost,
                    )
                )
            else:
                call.input_tokens, call.output_tokens = None, None
                call.cost = ticket["cost"]
                call.usage_basis = "unknown_reserved" if error else "estimated_reserved"
            call.status = "not_sent" if not dispatched else "unknown" if error else "succeeded"
            call.error_code = type(error).__name__[:80] if error else None
            call.latency_ms = int((time.monotonic() - ticket["started"]) * 1000)
            await db.execute(
                update(ModelConcurrencySlot)
                .where(
                    ModelConcurrencySlot.id == ticket["slot"],
                    ModelConcurrencySlot.call_id == ticket["id"],
                )
                .values(call_id=None, expires_at=None)
            )
            await db.commit()

    async def invoke(
        self,
        service,
        model,
        messages,
        target,
        purpose,
        schema_version="plain",
        extra_input_tokens=0,
        output_limit=None,
    ):
        if not settings.LLM_GATEWAY_ENABLED:
            async with service._semaphore:
                return await target.ainvoke(messages)
        ticket = await self.reserve(
            model,
            messages,
            purpose,
            output_limit or settings.LLM_MAX_TOKENS,
            schema_version,
            extra_input_tokens=extra_input_tokens,
        )
        dispatched = False
        raw = None
        try:
            if time.monotonic() >= ticket["deadline"]:
                raise TimeoutError("run_model_deadline_exceeded")
            timeout = min(
                service.TIMEOUT, max(0.01, ticket["deadline"] - time.monotonic())
            )
            call_deadline = time.monotonic() + timeout
            async with asyncio.timeout(timeout) as call_budget:
                async with service._semaphore:
                    if time.monotonic() >= call_deadline:
                        raise TimeoutError("run_model_deadline_exceeded")
                    dispatched = True
                    result = await target.ainvoke(messages)
            raw = (
                result.get("raw")
                if isinstance(result, dict) and "raw" in result
                else result
            )
            # 依赖可能吞掉CancelledError或阻塞事件循环；timeout退出不等于按时完成。
            if call_budget.expired() or time.monotonic() >= call_deadline:
                raise TimeoutError("late_model_result_rejected")
            await self.settle(ticket, raw)
            return result
        except BaseException as exc:
            await asyncio.shield(self.settle(ticket, raw, error=exc, dispatched=dispatched))
            raise


GATEWAY = ModelGateway()
