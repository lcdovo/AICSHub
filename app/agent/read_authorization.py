"""私有只读工具凭据：参数不能作为身份，调用时重新检查账号和角色。"""
from datetime import datetime, timedelta, timezone
import uuid

import jwt
from pydantic import BaseModel, ConfigDict, Field

from app.agent.ticket_authorization import request_hash
from app.config.config import settings


class OrderReadInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    user_id: int = Field(gt=0)
    order_id: str = Field(min_length=1, max_length=32)


class HistoryReadInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    user_id: int = Field(gt=0)
    limit: int = Field(default=10, ge=1, le=100)
    query: str = Field(default="", max_length=10000)
    session_id: str = Field(default="", max_length=36)


def authorize_read(name: str, payload: dict, actor_id: int) -> str:
    if type(actor_id) is not int or actor_id <= 0:
        raise ValueError("Invalid actor")
    return jwt.encode({"purpose": name, "sub": str(actor_id), "request_hash": request_hash(payload),
                       "exp": datetime.now(timezone.utc) + timedelta(minutes=2)},
                      settings.AUTH_SECRET_KEY, algorithm="HS256")


async def verify_read(name: str, payload: dict, authorization: str) -> None:
    from app.models.models import User, Session, OperationAudit
    from app.utils.database import async_session
    try:
        claims = jwt.decode(authorization, settings.AUTH_SECRET_KEY, algorithms=["HS256"],
                            options={"require": ["purpose", "sub", "request_hash", "exp"]})
        if claims["purpose"] != name or claims["request_hash"] != request_hash(payload):
            raise ValueError("Invalid read authorization")
        actor_id = int(claims["sub"])
        async with async_session() as db:
            actor = await db.get(User, actor_id)
            if (actor is None or not actor.status or actor.disabled_at is not None
                    or (actor_id != payload["user_id"] and actor.role != "admin")):
                raise ValueError("Read permission revoked")
            if payload.get("session_id"):
                session = await db.get(Session, payload["session_id"])
                if session is None or session.user_id != payload["user_id"]:
                    raise ValueError("Invalid read scope")
            if actor_id != payload["user_id"]:
                db.add(OperationAudit(operation=name, idempotency_key="read:" + uuid.uuid4().hex,
                                      user_id=payload["user_id"], request_payload={"actor_id": actor_id,
                                      "owner_id": payload["user_id"], "request_hash": request_hash(payload)}))
                await db.commit()
    except (jwt.InvalidTokenError, KeyError, TypeError) as exc:
        raise ValueError("Invalid read authorization") from exc
