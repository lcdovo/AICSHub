"""内部工单调用凭据：绑定已认证轮次、请求参数与持久幂等键。

凭据由工具节点签发，不接受模型提供；不能记录到Trace/工具历史或回复。
"""
from app.utils.utc_time import utc_now
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from pydantic import BaseModel, ConfigDict, Field

from app.config.config import settings


class TicketToolInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    user_id: int = Field(gt=0)
    session_id: str = Field(min_length=1, max_length=36)
    category: Literal["产品咨询", "订单问题", "退换货", "投诉建议", "技术支持"]
    content: str = Field(min_length=2, max_length=10000)
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    idempotency_key: str = Field(min_length=1, max_length=128)


class TicketUpdateToolInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    ticket_id: str = Field(min_length=1, max_length=36)
    status: Literal["pending", "processing", "resolved", "closed", "escalated"]
    note: str = Field(default="", max_length=10000)
    idempotency_key: str = Field(min_length=1, max_length=128)


def authorize_ticket_update(payload: dict, actor_id: int) -> str:
    """仅由已认证管理员适配层签发；普通聊天不自动选择更新工具。"""
    if type(actor_id) is not int or actor_id <= 0:
        raise ValueError("Invalid actor")
    return jwt.encode({"purpose": "update_ticket_status", "sub": str(actor_id),
                       "request_hash": request_hash(payload),
                       "exp": utc_now() + timedelta(minutes=2)},
                      settings.AUTH_SECRET_KEY, algorithm="HS256")


def verify_ticket_update_authorization(authorization: str, payload: dict) -> int:
    try:
        claims = jwt.decode(authorization, settings.AUTH_SECRET_KEY, algorithms=["HS256"],
                            options={"require": ["exp", "sub", "purpose", "request_hash"]})
        subject = claims["sub"]
        if (claims["purpose"] != "update_ticket_status" or claims["request_hash"] != request_hash(payload)
                or not isinstance(subject, str) or not subject.isascii() or not subject.isdecimal()
                or int(subject) <= 0):
            raise ValueError("Invalid update authorization")
        return int(subject)
    except jwt.InvalidTokenError as exc:
        raise ValueError("Invalid update authorization") from exc


def request_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def authorize_ticket(payload: dict, message_id: int, actor_id: int | None = None) -> str:
    """仅供可信Agent节点调用；不是公开的用户授权接口。"""
    if type(message_id) is not int or message_id <= 0:
        raise ValueError("Missing persisted source message")
    actor_id = payload["user_id"] if actor_id is None else actor_id
    if type(actor_id) is not int or actor_id <= 0:
        raise ValueError("Invalid actor")
    return jwt.encode({
        "purpose": "create_ticket", "sub": str(payload["user_id"]), "message_id": message_id,
        "actor_id": actor_id,
        "request_hash": request_hash(payload), "exp": utc_now() + timedelta(minutes=2),
    }, settings.AUTH_SECRET_KEY, algorithm="HS256")


def verify_ticket_authorization(authorization: str, payload: dict) -> tuple[int, int]:
    try:
        claims = jwt.decode(authorization, settings.AUTH_SECRET_KEY, algorithms=["HS256"],
                            options={"require": ["exp", "sub", "purpose", "message_id", "actor_id", "request_hash"]})
        if (claims["purpose"] != "create_ticket" or claims["sub"] != str(payload["user_id"])
                or claims["request_hash"] != request_hash(payload)
                or type(claims["message_id"]) is not int or claims["message_id"] <= 0
                or type(claims["actor_id"]) is not int or claims["actor_id"] <= 0):
            raise ValueError("Invalid ticket authorization")
        return claims["message_id"], claims["actor_id"]
    except jwt.InvalidTokenError as exc:
        raise ValueError("Invalid ticket authorization") from exc
