"""退款登记内部凭据：仅绑定当前明确确认轮次及精确提交参数。"""
from datetime import datetime, timedelta, timezone
import re
from typing import Literal

import jwt
from pydantic import BaseModel, ConfigDict, Field

from app.agent.ticket_authorization import request_hash
from app.config.config import settings

CONFIRMATIONS = {"确认", "同意", "确定", "提交", "是的", "可以"}

_CONFIRM_SENTENCES = {
    "确认提交这笔退货退款申请", "确认提交这笔退款申请", "我确认提交本次退货退款申请",
}
_CANCEL_SENTENCES = {
    "取消刚才的申请，不要提交", "取消刚才的申请,不要提交",
    "取消本次退款申请", "不要提交这笔退货退款申请",
}
_CANCELLATIONS = {"取消", "不同意", "先不退", "不提交", "算了", "false", "0"}


def _refund_sentence(message: str) -> str:
    value = message.strip()
    return value[:-1] if value and value[-1] in "。.!！" else value


def is_refund_confirmation_message(message: str, refund_type: str | None = None) -> bool:
    """Whole current-message grammar, never a keyword/LLM decision or parameter rewrite."""
    value = message.strip()
    if value in CONFIRMATIONS:
        return True
    sentence = _refund_sentence(value)
    if sentence not in _CONFIRM_SENTENCES:
        return False
    if refund_type is None:
        return True
    return refund_type == "return" if "退货退款" in sentence else refund_type in {"refund", "return"}


def is_refund_cancellation_message(message: str) -> bool:
    value = message.strip()
    return value in _CANCELLATIONS or _refund_sentence(value) in _CANCEL_SENTENCES


def refund_confirmation_issue(message: str, refund_type: str) -> str | None:
    """A current-preview reference may refuse/qualify confirmation, never grant it."""
    if is_refund_confirmation_message(message, refund_type) or is_refund_cancellation_message(message):
        return None
    if is_refund_confirmation_message(message):
        return "confirmation_type_mismatch"
    if re.search(r"确认(?:提交)?(?:这笔|本次)(?:退货退款|退款)申请", message):
        return "not_explicit_current_confirmation"
    return None


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
