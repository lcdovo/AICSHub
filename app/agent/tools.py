"""
核心工具实现 - Function Calling (Phase 3 增强版)
实现订单查询、工单创建、退换货申请等8个结构化工具
支持重试机制、参数校验、错误处理、执行追踪
"""
import uuid
import asyncio
import time
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, List, Callable, Tuple, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)


class ToolParameter(BaseModel):
    """工具参数定义"""
    name: str
    type: str
    description: str
    required: bool = True
    enum: Optional[List[str]] = None
    default: Any = None


class ToolDefinition(BaseModel):
    """工具定义"""
    name: str
    description: str
    parameters: List[ToolParameter]
    async_execute: Optional[Callable] = None
    sync_execute: Optional[Callable] = None


class ToolExecutionResult(BaseModel):
    """工具执行结果"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    hint: Optional[str] = None
    execution_time_ms: int = 0
    retry_count: int = 0
    error_type: Optional[str] = None


def _level_label(value: Any) -> str:
    """把用户等级枚举/字符串映射为中文展示名。"""
    mapping = {
        "normal": "普通用户",
        "vip": "VIP",
        "VIP": "VIP",
        "enterprise": "企业用户",
    }
    return mapping.get(str(value), str(value or "普通用户"))


class BaseTool:
    """工具基类 - 支持重试、超时、错误处理"""
    name: str = ""
    description: str = ""
    parameters: List[Dict[str, Any]] = []
    max_retries: int = 3
    retry_delay_ms: int = 100
    timeout_ms: int = 5000
    circuit_failure_threshold: int = 3
    circuit_recovery_seconds: int = 30
    cache_success: bool = True

    def __init__(self):
        self._idempotency_cache: Dict[str, ToolExecutionResult] = {}
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def validate_params(self, **kwargs) -> Tuple[bool, List[str]]:
        """参数验证，返回 (是否有效, 错误信息列表)"""
        errors = []
        for param in self.parameters:
            name = param["name"]
            if param.get("required", True) and name not in kwargs:
                errors.append(f"缺少必要参数: {name}")
            elif name in kwargs and kwargs[name] is None:
                errors.append(f"参数 {name} 不能为 None")

            if name in kwargs and name in [p["name"] for p in self.parameters if p.get("enum")]:
                param_def = next(p for p in self.parameters if p["name"] == name)
                if param_def.get("enum") and kwargs[name] not in param_def["enum"]:
                    errors.append(
                        f"参数 {name} 值无效: {kwargs[name]}, "
                        f"有效值为: {param_def['enum']}"
                    )

        return len(errors) == 0, errors

    async def execute_with_retry(self, **kwargs) -> ToolExecutionResult:
        """带重试的执行"""
        start_time = time.time()
        last_error = None

        if time.time() < self._circuit_open_until:
            return ToolExecutionResult(
                success=False,
                error="工具熔断器已开启，请稍后重试",
                error_type="circuit_open",
            )

        valid, errors = self.validate_params(**kwargs)
        if not valid:
            return ToolExecutionResult(
                success=False,
                error="；".join(errors),
                error_type="validation",
            )

        idempotency_key = str(kwargs.get("idempotency_key", "")).strip()
        if self.cache_success and idempotency_key and idempotency_key in self._idempotency_cache:
            return self._idempotency_cache[idempotency_key].model_copy(deep=True)

        for attempt in range(self.max_retries):
            try:
                if attempt > 0:
                    delay = self.retry_delay_ms * (2 ** (attempt - 1)) / 1000
                    await asyncio.sleep(delay)
                    logger.info(f"工具 {self.name} 第 {attempt + 1} 次重试")

                result = await asyncio.wait_for(
                    self.execute(**kwargs), timeout=self.timeout_ms / 1000
                )
                execution_time = int((time.time() - start_time) * 1000)

                if result.get("success", False):
                    self._consecutive_failures = 0
                    tool_result = ToolExecutionResult(
                        success=True,
                        data=result.get("data"),
                        execution_time_ms=execution_time,
                        retry_count=attempt,
                    )
                    if self.cache_success and idempotency_key:
                        self._idempotency_cache[idempotency_key] = tool_result.model_copy(deep=True)
                    return tool_result
                else:
                    last_error = result.get("error", "未知错误")
                    # 业务校验/权限错误不可通过重试修复；只有适配器明确标记的瞬时错误才重试。
                    if result.get("retryable", False) and attempt < self.max_retries - 1:
                        continue
                    return ToolExecutionResult(
                        success=False,
                        error=last_error,
                        hint=result.get("hint"),
                        execution_time_ms=execution_time,
                        retry_count=attempt,
                        error_type=result.get("error_type", "business"),
                    )

            except asyncio.TimeoutError:
                last_error = f"执行超时 (>{self.timeout_ms}ms)"
                self._consecutive_failures += 1
                if attempt < self.max_retries - 1:
                    continue

            except Exception as e:
                last_error = "工具依赖或执行服务异常，请按原请求核查结果"
                self._consecutive_failures += 1
                logger.error("工具 %s 执行异常: %s", self.name, type(e).__name__)
                if attempt < self.max_retries - 1:
                    continue

        if self._consecutive_failures >= self.circuit_failure_threshold:
            self._circuit_open_until = time.time() + self.circuit_recovery_seconds

        execution_time = int((time.time() - start_time) * 1000)
        return ToolExecutionResult(
            success=False,
            error=last_error or "未知错误",
            hint="请稍后重试或联系人工客服",
            execution_time_ms=execution_time,
            retry_count=self.max_retries,
            error_type="timeout" if "超时" in (last_error or "") else "execution",
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError


class QueryOrderTool(BaseTool):
    """查询订单状态与物流"""
    name = "query_order"
    description = "根据订单号查询订单状态、物流信息、订单详情"
    parameters = [
        {"name": "order_id", "type": "string", "description": "订单号 (如 ORD20260801)", "required": True},
        {"name": "user_id", "type": "integer", "description": "当前认证用户 ID", "required": True},
    ]

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from app.agent.read_authorization import OrderReadInput, verify_read
        values = dict(kwargs)
        authorization = values.pop("authorization", "")
        try:
            payload = OrderReadInput.model_validate(values).model_dump()
            await verify_read(self.name, payload, authorization)
        except (ValidationError, ValueError, TypeError):
            return {"success": False, "error": "订单查询参数或身份授权无效", "error_type": "permission"}
        order_id = payload["order_id"].strip().upper()
        user_id = payload["user_id"]

        if not order_id:
            return {
                "success": False,
                "error": "订单号不能为空",
                "hint": "请提供有效的订单号，如 ORD20260801",
            }

        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
            return {"success": False, "error": "无权访问该订单", "error_type": "permission"}

        # Prefer the persisted order projection. Schema 由启动期 init_database()/Alembic
        # 统一保证，工具执行路径不再重复执行 DDL。
        from app.utils.database import async_session
        from app.models.models import Order
        from sqlalchemy import select
        async with async_session() as session:
            # 在数据库层同时限定订单号和当前用户，防止仅凭他人订单号读取详情。
            order_row = await session.scalar(
                select(Order).where(Order.order_id == order_id, Order.user_id == user_id)
            )
            if order_row is not None:
                order = {
                    "order_id": order_row.order_id, "status": order_row.status,
                    "status_code": order_row.status_code, "items": order_row.items or [],
                    "total_amount": format(order_row.total_amount, ".2f"), "shipping": order_row.shipping,
                    "created_at": order_row.created_at.isoformat() if order_row.created_at else "",
                    "pay_method": order_row.pay_method, "address": order_row.address,
                }
                deadline = order_row.received_at + timedelta(days=7) if order_row.received_at else None
                can_refund = (order_row.status_code in {"shipped", "delivered", "completed"}
                              and deadline is not None and order_row.received_at <= datetime.utcnow() <= deadline)
                return {
                    "success": True, "data": order, "can_refund": can_refund,
                    "refund_deadline": deadline.isoformat() if deadline else None,
                }

        # No in-code order fallback: missing rows are reported to the caller.
        return {"success": False, "error": f"未找到订单 {order_id}", "hint": "请确认订单号是否正确"}


class CreateTicketTool(BaseTool):
    """创建持久工单；内部签名凭据绑定可信轮次，不接受裸user_id授权。"""
    name = "create_ticket"
    max_retries = 1
    cache_success = False
    description = "创建客服工单，用于处理用户投诉、咨询等问题"
    parameters = [
        {"name": "user_id", "type": "integer", "description": "用户ID", "required": True},
        {"name": "category", "type": "string", "description": "工单分类", "required": True,
         "enum": ["产品咨询", "订单问题", "退换货", "投诉建议", "技术支持"]},
        {"name": "content", "type": "string", "description": "工单内容", "required": True},
        {"name": "priority", "type": "string", "description": "优先级", "required": False,
         "enum": ["low", "medium", "high", "urgent"], "default": "medium"},
    ]

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from fastapi import HTTPException
        from pydantic import ValidationError
        from app.agent.ticket_authorization import TicketToolInput, verify_ticket_authorization
        from app.models.models import User, Message, MessageRole, Session
        from app.services.ticket_creation import create_request, resolve_idempotency_key, ticket_receipt
        from app.utils.database import async_session

        values = dict(kwargs)
        authorization = values.pop("authorization", "")
        try:
            payload = TicketToolInput.model_validate(values).model_dump()
            if len(payload["content"].strip()) < 2:
                raise ValueError("Empty ticket content")
            resolve_idempotency_key(payload["idempotency_key"], None)
            message_id, actor_id = verify_ticket_authorization(authorization, payload)
        except (ValidationError, ValueError, TypeError, HTTPException):
            return {"success": False, "error": "工单参数或执行授权无效", "error_type": "validation", "retryable": False}

        try:
            async with async_session() as db:
                actor = await db.get(User, actor_id)
                source = await db.get(Message, message_id)
                session = await db.get(Session, payload["session_id"])
                if (actor is None or not actor.status or actor.disabled_at is not None
                        or session is None or session.user_id != payload["user_id"]
                        or source is None or source.session_id != session.id or source.role != MessageRole.USER):
                    return {"success": False, "error": "无权执行此工单请求", "error_type": "permission", "retryable": False}
                from app.services.run_execution import guard_write
                await guard_write(db, source)
                key = payload.pop("idempotency_key")
                item, _ = await create_request(db, actor, "create_ticket", payload, key)
                receipt = ticket_receipt(item)
                labels = {"pending": "待处理", "processing": "处理中", "resolved": "已解决",
                          "closed": "已关闭", "escalated": "已升级"}
                receipt["message"] = f"工单 {item.id} 已登记，当前状态：{labels[item.status.value]}。"
                return {"success": True, "data": receipt}
        except HTTPException as exc:
            return {"success": False, "error": exc.detail,
                    "error_type": "conflict" if exc.status_code == 409 else "permission", "retryable": False}
        except Exception:
            # 可能在提交后丢失回包，不能编造ID或宣称确定未执行。原键可重查持久回执。
            return {"success": False, "error": "工单服务暂时不可用，请使用原请求核查结果",
                    "error_type": "dependency_unavailable", "retryable": False}


class ApplyRefundTool(BaseTool):
    """退换货申请"""
    name = "apply_refund"
    max_retries = 1
    cache_success = False
    description = "登记模拟待审核退换货申请，需要本轮确认与执行凭据；不执行真实支付"
    parameters = [
        {"name": "order_id", "type": "string", "description": "订单号", "required": True},
        {"name": "reason", "type": "string", "description": "退换货原因", "required": True},
        {"name": "type", "type": "string", "description": "类型：refund(仅退款)、return(退货退款)、exchange(换货)", "required": False,
         "enum": ["refund", "return", "exchange"], "default": "refund"},
        {"name": "user_id", "type": "integer", "description": "资源所属用户ID", "required": False, "default": 0},
        {"name": "idempotency_key", "type": "string", "description": "幂等键", "required": False, "default": ""},
    ]

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from fastapi import HTTPException
        from app.agent.refund_authorization import RefundToolInput, verify_refund
        from app.services.refund_application import submit
        from app.services.ticket_creation import resolve_idempotency_key
        from app.utils.database import async_session

        values = dict(kwargs)
        authorization = values.pop("authorization", "")
        try:
            payload = RefundToolInput.model_validate(values).model_dump()
            if not payload["order_id"].strip() or len(payload["reason"].strip()) < 3:
                raise ValueError("Invalid refund parameters")
            resolve_idempotency_key(payload["idempotency_key"], None)
            signed_payload = {k: v for k, v in payload.items() if v is not None}
            message_id, actor_id = verify_refund(authorization, signed_payload)
            payload["order_id"] = payload["order_id"].strip().upper()
            payload["reason"] = payload["reason"].strip()
        except (ValidationError, ValueError, TypeError, HTTPException):
            return {"success": False, "error": "退款参数或本轮执行授权无效", "error_type": "validation", "retryable": False}
        try:
            data = await submit(async_session, payload, message_id, actor_id)
            return {"success": True, "data": data}
        except HTTPException as exc:
            return {"success": False, "error": exc.detail,
                    "error_type": {403: "permission", 404: "not_found", 409: "conflict"}.get(exc.status_code, "validation"),
                    "retryable": False}
        except Exception:
            return {"success": False, "error": "退款申请服务暂时不可用，请使用原请求核查结果",
                    "error_type": "dependency_unavailable", "retryable": False}


class SearchKBTool(BaseTool):
    """知识库检索 - 支持关键词匹配 + 评分排序"""
    name = "search_kb"
    description = "从知识库中检索相关问题的答案，支持关键词匹配和语义搜索"
    parameters = [
        {"name": "query", "type": "string", "description": "查询内容", "required": True},
        {"name": "top_k", "type": "integer", "description": "返回结果数量", "required": False, "default": 3},
    ]

    KNOWLEDGE_BASE = {
        "退换货政策": {
            "content": "我们支持 7 天无理由退换货。商品需保持原包装、未经使用。退款将在收到商品后 3-7 个工作日内到账。",
            "keywords": ["退换货", "退货", "退款", "7天", "无理由", "售后"],
            "category": "售后政策",
        },
        "订单查询": {
            "content": "您可以通过订单号或手机号查询订单状态。物流信息在发货后 24 小时内更新。支持按时间段查询历史订单。",
            "keywords": ["订单", "物流", "查询", "发货", "快递", "状态"],
            "category": "订单服务",
        },
        "会员权益": {
            "content": "VIP 会员享受 95 折优惠、专属客服优先接入、免费包邮等权益。年度会员费 199 元，开通后立即生效。",
            "keywords": ["会员", "VIP", "权益", "优惠", "包邮", "折扣"],
            "category": "会员服务",
        },
        "支付方式": {
            "content": "我们支持支付宝、微信支付、银联、信用卡分期（3/6/12期）等多种支付方式。大额订单支持企业对公转账。",
            "keywords": ["支付", "付款", "支付宝", "微信", "银行卡", "分期"],
            "category": "支付服务",
        },
        "发票开具": {
            "content": "购买后 180 天内可申请开具电子发票。请在订单详情页点击'申请发票'按钮填写抬头信息。增值税专用发票需额外提交资质材料。",
            "keywords": ["发票", "开票", "抬头", "电子发票", "增值税"],
            "category": "财务服务",
        },
        "物流配送": {
            "content": "全国大部分地区 1-3 个工作日送达，偏远地区可能需要 3-5 个工作日。提供顺丰、京东、中通等多家快递公司选择。",
            "keywords": ["物流", "配送", "快递", "送达", "发货", "时效"],
            "category": "物流服务",
        },
        "账号安全": {
            "content": "建议使用强密码（至少8位，包含大小写字母和数字）。如怀疑账号被盗，请立即通过'忘记密码'功能重置密码，并联系客服冻结账号。",
            "keywords": ["密码", "账号", "安全", "登录", "被盗", "冻结"],
            "category": "账号服务",
        },
        "产品使用": {
            "content": "产品首次使用请先充电 2 小时。长按电源键 3 秒开机。详细使用说明请参考产品包装盒内的《快速上手指南》。",
            "keywords": ["使用", "开机", "充电", "设置", "安装", "教程"],
            "category": "产品支持",
        },
        "售后服务": {
            "content": "所有产品享受 1 年免费质保。非人为损坏可享受免费维修或更换。质保期外提供有偿维修服务，费用按故障类型评估。",
            "keywords": ["保修", "质保", "维修", "售后", "故障", "损坏"],
            "category": "售后服务",
        },
        "活动规则": {
            "content": "新用户首单立减 20 元。满 199 减 30，满 399 减 80。促销活动不与其他优惠同享，具体以结算页为准。",
            "keywords": ["优惠", "活动", "促销", "折扣", "满减", "券"],
            "category": "营销活动",
        },
    }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        query = kwargs.get("query", "").lower().strip()
        top_k = kwargs.get("top_k", 3)

        if not query:
            return {
                "success": True,
                "data": [],
                "hint": "请输入搜索关键词",
            }

        query_chars = set(query)
        results = []

        for title, info in self.KNOWLEDGE_BASE.items():
            score = 0
            title_lower = title.lower()
            content_lower = info["content"].lower()

            if query in title_lower:
                score += 10
            if query in content_lower:
                score += 5

            for keyword in info["keywords"]:
                keyword_lower = keyword.lower()
                if keyword_lower in query or query in keyword_lower:
                    score += 3
                overlap = len(query_chars & set(keyword_lower))
                score += overlap * 0.5

            if score > 0:
                results.append({
                    "title": title,
                    "content": info["content"],
                    "category": info.get("category", "通用"),
                    "relevance_score": round(score, 2),
                })

        results.sort(key=lambda x: x["relevance_score"], reverse=True)
        results = results[:top_k]

        if results:
            return {"success": True, "data": results}
        else:
            return {
                "success": True,
                "data": [],
                "hint": "未找到相关内容，请尝试其他关键词，或联系人工客服咨询",
            }


class EscalateToHumanTool(BaseTool):
    """持久登记人工请求，不承诺已接通或虚构等待时间。"""
    name = "escalate_to_human"
    max_retries = 1
    cache_success = False
    description = "登记可查询的人工处理请求；登记不代表已接通客服"
    parameters = [
        {"name": "reason", "type": "string", "description": "转接原因", "required": True},
        {"name": "priority", "type": "string", "description": "优先级", "required": False,
         "enum": ["normal", "urgent"], "default": "normal"},
        {"name": "context", "type": "string", "description": "上下文信息（可选）", "required": False, "default": ""},
        {"name": "user_id", "type": "integer", "description": "用户ID", "required": False, "default": 0},
        {"name": "session_id", "type": "string", "description": "会话ID", "required": False, "default": ""},
    ]

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from fastapi import HTTPException
        from app.agent.handoff_authorization import HandoffToolInput, verify_handoff_authorization
        from app.models.models import User, Message, MessageRole, Session
        from app.services.ticket_creation import create_request, resolve_idempotency_key, handoff_receipt, handoff_message
        from app.utils.database import async_session

        values = dict(kwargs)
        authorization = values.pop("authorization", "")
        try:
            payload = HandoffToolInput.model_validate(values).model_dump()
            if not payload["reason"].strip():
                raise ValueError("Missing handoff reason")
            resolve_idempotency_key(payload["idempotency_key"], None)
            message_id, actor_id = verify_handoff_authorization(authorization, payload)
        except (ValidationError, ValueError, TypeError, HTTPException):
            return {"success": False, "error": "转人工参数或执行授权无效", "error_type": "validation", "retryable": False}
        try:
            async with async_session() as db:
                actor = await db.get(User, actor_id)
                source = await db.get(Message, message_id)
                session = await db.get(Session, payload["session_id"])
                if (actor is None or not actor.status or actor.disabled_at is not None
                        or session is None or session.user_id != payload["user_id"]
                        or source is None or source.session_id != session.id or source.role != MessageRole.USER):
                    return {"success": False, "error": "无权执行此人工请求", "error_type": "permission", "retryable": False}
                from app.services.run_execution import guard_write
                await guard_write(db, source)
                key = payload.pop("idempotency_key")
                payload["context"] = {"context": payload["context"]} if payload["context"] else {}
                item, _ = await create_request(db, actor, "request_handoff", payload, key)
                receipt = handoff_receipt(item)
                receipt.update(escalation_id=item.id, message=handoff_message(receipt))
                return {"success": True, "data": receipt}
        except HTTPException as exc:
            return {"success": False, "error": exc.detail,
                    "error_type": {409: "conflict", 404: "not_found"}.get(exc.status_code, "permission"), "retryable": False}
        except Exception:
            return {"success": False, "error": "人工请求服务暂时不可用，请使用原请求核查结果",
                    "error_type": "dependency_unavailable", "retryable": False}


class NotificationToolInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    channel: Literal["sms", "email", "inapp"]
    content: str = Field(min_length=5, max_length=10000)
    # 保留旧MCP省略user_id的调用格式；当前不会实际投递或授权任何身份。
    user_id: int = Field(default=0, ge=0)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=128)


class SendNotificationTool(BaseTool):
    """发送能力尚未接入：明确拒绝，不构造投递回执。"""
    name = "send_notification"
    max_retries = 1
    cache_success = False
    description = "通知发送入口；当前未接入投递适配器，返回能力不可用"
    parameters = [
        {"name": "channel", "type": "string", "description": "通知渠道", "required": True,
         "enum": ["sms", "email", "inapp"]},
        {"name": "content", "type": "string", "description": "通知内容", "required": True},
        {"name": "user_id", "type": "integer", "description": "用户ID", "required": False, "default": 0},
    ]

    async def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            payload = NotificationToolInput.model_validate(kwargs)
            if len(payload.content.strip()) < 5 or (
                payload.idempotency_key is not None and not payload.idempotency_key.strip()
            ):
                raise ValueError("Invalid notification input")
        except (ValidationError, ValueError, TypeError):
            return {"success": False, "error": "通知参数无效", "error_type": "validation", "retryable": False}
        # 现有outbox只有事件记录，没有实际投递适配器/消费worker。
        # 不写库、不生成ID；入队不能被当作“发送成功”的替代事实。
        return {"success": False, "error": "通知发送能力未接入，未发送通知",
                "error_type": "channel_unavailable", "retryable": False}


class UpdateTicketStatusTool(BaseTool):
    """更新工单状态"""
    name = "update_ticket_status"
    max_retries = 1
    cache_success = False
    description = "更新工单的处理状态，用于工单流转和状态同步"
    parameters = [
        {"name": "ticket_id", "type": "string", "description": "工单ID", "required": True},
        {"name": "status", "type": "string", "description": "新状态", "required": True,
         "enum": ["pending", "processing", "resolved", "closed", "escalated"]},
        {"name": "note", "type": "string", "description": "状态变更备注", "required": False, "default": ""},
    ]

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from fastapi import HTTPException
        from pydantic import ValidationError
        from app.agent.ticket_authorization import TicketUpdateToolInput, verify_ticket_update_authorization
        from app.models.models import User
        from app.services.ticket_creation import resolve_idempotency_key
        from app.services.ticket_update import update_ticket
        from app.utils.database import async_session

        values = dict(kwargs)
        authorization = values.pop("authorization", "")
        try:
            payload = TicketUpdateToolInput.model_validate(values).model_dump()
            resolve_idempotency_key(payload["idempotency_key"], None)
            actor_id = verify_ticket_update_authorization(authorization, payload)
        except (ValidationError, ValueError, TypeError, HTTPException):
            return {"success": False, "error": "工单更新参数或执行授权无效", "error_type": "validation", "retryable": False}
        try:
            async with async_session() as db:
                actor = await db.get(User, actor_id)
                if actor is None:
                    return {"success": False, "error": "操作人不存在", "error_type": "permission", "retryable": False}
                receipt = await update_ticket(db, actor, payload["ticket_id"], {"status": payload["status"]},
                                              note=payload["note"], key=payload["idempotency_key"])
                labels = {"pending": "待处理", "processing": "处理中", "resolved": "已解决",
                          "closed": "已关闭", "escalated": "已升级"}
                return {"success": True, "data": {**receipt["ticket"], "old_status": receipt["old_status"],
                        "new_status": receipt["new_status"], "new_status_label": labels[receipt["new_status"]],
                        "note": receipt["note"], "message": f"本次工单更新已提交，状态：{labels[receipt['new_status']]}。"}}
        except HTTPException as exc:
            return {"success": False, "error": exc.detail,
                    "error_type": {403: "permission", 404: "not_found", 409: "conflict"}.get(exc.status_code, "validation"),
                    "retryable": False}
        except Exception:
            return {"success": False, "error": "工单更新服务暂时不可用，请使用原请求核查结果",
                    "error_type": "dependency_unavailable", "retryable": False}


class GetUserHistoryTool(BaseTool):
    """获取用户历史咨询记录"""
    name = "get_user_history"
    description = "获取用户的历史咨询记录、工单历史、偏好标签等（跨会话长期记忆/用户画像）"
    parameters = [
        {"name": "user_id", "type": "integer", "description": "用户ID", "required": True},
        {"name": "limit", "type": "integer", "description": "返回条数", "required": False, "default": 10},
        {"name": "query", "type": "string", "description": "用于语义回忆的当前问题（可选）", "required": False, "default": ""},
    ]

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from app.agent.read_authorization import HistoryReadInput, verify_read
        values = dict(kwargs)
        authorization = values.pop("authorization", "")
        try:
            payload = HistoryReadInput.model_validate(values).model_dump()
            await verify_read(self.name, payload, authorization)
        except (ValidationError, ValueError, TypeError):
            return {"success": False, "error": "历史查询参数或身份授权无效", "error_type": "permission"}
        user_id, limit, query, session_id = (payload[k] for k in ("user_id", "limit", "query", "session_id"))

        if not user_id or user_id <= 0:
            return {"success": False, "error": "请提供有效的用户ID", "hint": "用户ID应为正整数"}

        # 跨会话长期记忆 / 用户画像：聚合 SQLite 历史，Embedding 可用时做向量回忆。
        try:
            from app.services.user_memory import get_user_memory_service

            profile = await get_user_memory_service().get_user_profile(
                user_id=user_id, query=query, session_id=session_id
            )
            level = _level_label(profile.get("user_level", "normal"))
            history = [
                {
                    "date": (item.get("timestamp") or "")[:10],
                    "type": "用户咨询" if item.get("role") == "user" else "客服回复",
                    "summary": str(item.get("content", ""))[:200],
                    "resolved": True,
                }
                for item in (profile.get("history") or [])
            ]
            return {
                "success": True,
                "data": {
                    "user_id": user_id,
                    "user_level": level,
                    "total_sessions": profile.get("total_sessions", 0),
                    "total_tickets": profile.get("total_tickets", 0),
                    "tags": profile.get("tags", []),
                    "history": history[:limit],
                    "recalled": (profile.get("recalled") or [])[:limit],
                    "memory_source": profile.get("memory_source", "degraded"),
                },
            }
        except Exception as exc:
            logger.warning("用户历史查询失败: %s", type(exc).__name__)
            return {
                "success": False,
                "error": "用户历史查询失败",
                "hint": "请稍后重试或转人工处理",
            }


TOOL_REGISTRY: Dict[str, BaseTool] = {
    QueryOrderTool.name: QueryOrderTool(),
    CreateTicketTool.name: CreateTicketTool(),
    ApplyRefundTool.name: ApplyRefundTool(),
    SearchKBTool.name: SearchKBTool(),
    EscalateToHumanTool.name: EscalateToHumanTool(),
    SendNotificationTool.name: SendNotificationTool(),
    UpdateTicketStatusTool.name: UpdateTicketStatusTool(),
    GetUserHistoryTool.name: GetUserHistoryTool(),
}


def get_all_tools() -> List[BaseTool]:
    return list(TOOL_REGISTRY.values())
