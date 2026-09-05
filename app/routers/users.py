from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Body, Query, Request
import json
from urllib.parse import parse_qs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.config import settings
from app.models.models import User, UserLevel, UserRole, RefreshToken
from app.schemas.schemas import UserCreate, UserUpdate, APIResponse, RefreshRequest, LogoutRequest
from app.security import (
    create_access_token, decode_token, ensure_owner_or_admin, enforce_rate_limit,
    get_current_user, hash_password, issue_refresh_token, token_digest, verify_password,
    require_admin,
)
from app.utils.database import get_db

router = APIRouter(prefix="/api/v1/users", tags=["用户管理"])


def _user_payload(user: User) -> dict:
    return {"id": user.id, "username": user.username, "nickname": user.nickname or user.username,
            "level": user.level.value if hasattr(user.level, "value") else user.level,
            "role": user.role or UserRole.USER.value, "status": bool(user.status)}


@router.post("/login", response_model=APIResponse)
async def login(request: Request, username: Optional[str] = Query(default=None), password: Optional[str] = Query(default=None), db: AsyncSession = Depends(get_db)):
    """使用用户名和密码登录；客户端传入 role 参数会被忽略。"""
    # 同时兼容 JSON、表单和旧版 query 客户端，但不再接受 role 作为权限来源。
    if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        form = parse_qs((await request.body()).decode("utf-8"))
        username = (form.get("username") or [username])[0]
        password = (form.get("password") or [password])[0]
    elif request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = json.loads((await request.body()).decode("utf-8"))
            username, password = body.get("username", username), body.get("password", password)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    # 按 IP + 账号限流，避免同一测试/办公出口下的不同用户互相阻塞；未知账号仍共享 IP 桶。
    host = request.client.host if request.client else "unknown"
    enforce_rate_limit(f"login:{host}:{username or 'unknown'}", settings.LOGIN_RATE_LIMIT_PER_MINUTE)
    if not username or not password:
        raise HTTPException(status_code=400, detail="请输入用户名和密码")
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not user.status or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    access_token = create_access_token(user)
    refresh_token, _ = await issue_refresh_token(db, user)
    await db.commit()
    return APIResponse(code=0, message="登录成功", data={**_user_payload(user), "access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer", "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60})


@router.post("/refresh", response_model=APIResponse)
async def refresh_token(request: RefreshRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(request.refresh_token, "refresh")
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_digest(request.refresh_token)))
    stored = result.scalar_one_or_none()
    now = datetime.utcnow()
    if not stored or stored.revoked_at or stored.expires_at <= now or str(payload.get("sub")) != str(stored.user_id):
        raise HTTPException(status_code=401, detail="Refresh Token 无效或已撤销")
    user = await db.get(User, stored.user_id)
    if not user or not user.status:
        raise HTTPException(status_code=401, detail="用户已禁用或不存在")
    stored.revoked_at = now
    access = create_access_token(user)
    new_refresh, replacement = await issue_refresh_token(db, user)
    stored.replaced_by = replacement.id
    await db.commit()
    return APIResponse(code=0, message="刷新成功", data={**_user_payload(user), "access_token": access, "refresh_token": new_refresh, "token_type": "bearer", "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60})


@router.post("/logout", response_model=APIResponse)
async def logout(request: LogoutRequest | None = Body(default=None), current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if request and request.refresh_token:
        result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_digest(request.refresh_token), RefreshToken.user_id == current_user.id))
        stored = result.scalar_one_or_none()
        if stored and not stored.revoked_at:
            stored.revoked_at = datetime.utcnow()
    current_user.token_version += 1
    await db.commit()
    return APIResponse(code=0, message="已退出登录", data=None)


@router.post("/", response_model=APIResponse)
async def create_user(user_data: UserCreate, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.username == user_data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")
    try:
        level = UserLevel(user_data.level)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="用户等级无效") from exc
    user = User(username=user_data.username, nickname=user_data.nickname, level=level, password_hash=hash_password(user_data.password), role=UserRole.USER.value)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return APIResponse(code=0, message="创建成功", data=_user_payload(user))


@router.get("/", response_model=APIResponse)
async def list_users(page: int = 1, page_size: int = 20, level: Optional[str] = None, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    query = select(User)
    if level:
        query = query.where(User.level == UserLevel(level))
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    return APIResponse(code=0, message="获取成功", data=[_user_payload(u) for u in result.scalars().all()])


@router.get("/{user_id}", response_model=APIResponse)
async def get_user(user_id: int, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    ensure_owner_or_admin(current_user, user_id)
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return APIResponse(code=0, message="获取成功", data={**_user_payload(user), "avatar_url": user.avatar_url, "tags": user.tags, "created_at": user.created_at.isoformat() if user.created_at else None})


@router.put("/{user_id}", response_model=APIResponse)
async def update_user(user_id: int, user_data: UserUpdate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    ensure_owner_or_admin(current_user, user_id)
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    for key, value in user_data.model_dump(exclude_unset=True).items():
        if key == "level":
            if current_user.role != UserRole.ADMIN.value:
                raise HTTPException(status_code=403, detail="只有管理员可以修改用户等级")
            value = UserLevel(value)
        if value is not None:
            setattr(user, key, value)
    user.updated_at = datetime.utcnow()
    await db.commit()
    return APIResponse(code=0, message="更新成功", data={"id": user.id})


@router.delete("/{user_id}", response_model=APIResponse)
async def delete_user(user_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.status = False
    user.disabled_at = datetime.utcnow()
    user.token_version += 1
    await db.commit()
    return APIResponse(code=0, message="删除成功", data=None)
