"""人工请求的内部轮次凭据，与工单凭据隔离用途。"""
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from pydantic import BaseModel, ConfigDict, Field

from app.agent.ticket_authorization import request_hash
from app.config.config import settings


class HandoffToolInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    user_id: int = Field(gt=0)
    session_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=1, max_length=100)
    priority: Literal["normal", "urgent"] = "normal"
    context: str = Field(default="", max_length=10000)
    idempotency_key: str = Field(min_length=1, max_length=128)


def authorize_handoff(payload: dict, message_id: int, actor_id: int | None = None) -> str:
    if type(message_id) is not int or message_id <= 0:
        raise ValueError("Missing source message")
    actor_id = payload["user_id"] if actor_id is None else actor_id
    if type(actor_id) is not int or actor_id <= 0:
        raise ValueError("Invalid actor")
    return jwt.encode({"purpose": "request_handoff", "sub": str(payload["user_id"]),
                       "actor_id": actor_id, "message_id": message_id, "request_hash": request_hash(payload),
                       "exp": datetime.now(timezone.utc) + timedelta(minutes=2)},
                      settings.AUTH_SECRET_KEY, algorithm="HS256")


def verify_handoff_authorization(authorization: str, payload: dict) -> tuple[int, int]:
    try:
        claims = jwt.decode(authorization, settings.AUTH_SECRET_KEY, algorithms=["HS256"],
                            options={"require": ["exp", "purpose", "sub", "actor_id", "message_id", "request_hash"]})
        if (claims["purpose"] != "request_handoff" or claims["sub"] != str(payload["user_id"])
                or claims["request_hash"] != request_hash(payload)
                or type(claims["actor_id"]) is not int or claims["actor_id"] <= 0
                or type(claims["message_id"]) is not int or claims["message_id"] <= 0):
            raise ValueError("Invalid handoff authorization")
        return claims["message_id"], claims["actor_id"]
    except jwt.InvalidTokenError as exc:
        raise ValueError("Invalid handoff authorization") from exc
