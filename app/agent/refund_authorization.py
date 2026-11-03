"""退款登记内部凭据：仅绑定当前明确确认轮次及精确提交参数。"""
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from pydantic import BaseModel, ConfigDict, Field

from app.agent.ticket_authorization import request_hash
from app.config.config import settings

CONFIRMATIONS = {"确认", "同意", "确定", "提交", "是的", "可以"}


class RefundToolInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    order_id: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=3, max_length=10000)
    type: Literal["refund", "return", "exchange"] = "refund"
    user_id: int = Field(gt=0)
    session_id: str = Field(min_length=1, max_length=36)
    idempotency_key: str = Field(min_length=1, max_length=128)
    action_id: str | None = None
    action_version: int | None = None


def authorize_refund(payload: dict, message_id: int, actor_id: int | None = None) -> str:
    if type(message_id) is not int or message_id <= 0:
        raise ValueError("Missing persisted confirmation")
    actor_id = payload["user_id"] if actor_id is None else actor_id
    return jwt.encode({"purpose": "apply_refund", "sub": str(payload["user_id"]), "actor_id": actor_id,
                       "message_id": message_id, "request_hash": request_hash(payload),
                       "exp": datetime.now(timezone.utc) + timedelta(minutes=2)},
                      settings.AUTH_SECRET_KEY, algorithm="HS256")


def verify_refund(authorization: str, payload: dict) -> tuple[int, int]:
    try:
        claims = jwt.decode(authorization, settings.AUTH_SECRET_KEY, algorithms=["HS256"],
                            options={"require": ["purpose", "sub", "actor_id", "message_id", "request_hash", "exp"]})
        if (claims["purpose"] != "apply_refund" or claims["sub"] != str(payload["user_id"])
                or claims["request_hash"] != request_hash(payload)
                or type(claims["actor_id"]) is not int or claims["actor_id"] <= 0
                or type(claims["message_id"]) is not int or claims["message_id"] <= 0):
            raise ValueError("Invalid refund authorization")
        return claims["message_id"], claims["actor_id"]
    except jwt.InvalidTokenError as exc:
        raise ValueError("Invalid refund authorization") from exc
