"""认证、密码哈希、RBAC 与本地限流。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.config import settings
from app.models.models import RefreshToken, User, UserRole
from app.utils.database import get_db

JWT_ALGORITHM = "HS256"
PASSWORD_ITERATIONS = 600_000
_bearer = HTTPBearer(auto_error=False)
_rate_events: dict[str, deque[float]] = defaultdict(deque)
_rate_lock = threading.Lock()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.urlsafe_b64decode(salt.encode("ascii")),
            int(iterations),
        )
        return hmac.compare_digest(actual, base64.urlsafe_b64decode(expected.encode("ascii")))
    except (TypeError, ValueError):
        return False


def _encode_token(user: User, token_type: str, expires_delta: timedelta, token_id: str | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "type": token_type,
        "role": user.role,
        "ver": user.token_version,
        "iat": now,
        "exp": now + expires_delta,
        "jti": token_id or str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.AUTH_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_access_token(user: User) -> str:
    return _encode_token(user, "access", timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def issue_refresh_token(db: AsyncSession, user: User) -> tuple[str, RefreshToken]:
    token_id = str(uuid.uuid4())
    expires_at = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    token = _encode_token(user, "refresh", timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS), token_id)
    record = RefreshToken(
        id=token_id,
        user_id=user.id,
        token_hash=token_digest(token),
        expires_at=expires_at,
    )
    db.add(record)
    await db.flush()
    return token, record


def decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(
            token, settings.AUTH_SECRET_KEY, algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub", "type", "ver"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 已过期") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 无效") from exc
    if payload.get("type") != expected_type:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 类型无效")
    subject = payload.get("sub")
    if (not isinstance(subject, str) or not subject.isascii() or not subject.isdecimal()
            or int(subject) <= 0 or type(payload.get("ver")) is not int or payload["ver"] < 0):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 无效")
    return payload


async def user_for_token(payload: dict, db: AsyncSession) -> User:
    """读取数据库最新身份，不信任JWT中的role或Session身份缓存。"""
    user = await db.get(User, int(payload["sub"]), populate_existing=True)
    if not user or not user.status or user.disabled_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户已禁用或不存在")
    if payload["ver"] != user.token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 已失效")
    return user


async def authenticate_access_token(token: str, db: AsyncSession) -> User:
    return await user_for_token(decode_token(token, "access"), db)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return await authenticate_access_token(credentials.credentials, db)


async def require_user(current_user: User = Depends(get_current_user)) -> User:
    return current_user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return current_user


def ensure_owner_or_admin(current_user: User, owner_id: int) -> None:
    if current_user.role != UserRole.ADMIN.value and current_user.id != owner_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该资源")


def enforce_rate_limit(key: str, limit: int, window_seconds: int = 60) -> None:
    """进程内降级限流；生产环境应改用 Redis 共享计数。"""
    now = time.monotonic()
    with _rate_lock:
        events = _rate_events[key]
        cutoff = now - window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= limit:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="请求过于频繁，请稍后重试")
        events.append(now)


def client_rate_key(request: Request, bucket: str, identity: str = "") -> str:
    client = request.client.host if request.client else "unknown"
    return f"{bucket}:{client}:{identity}"
