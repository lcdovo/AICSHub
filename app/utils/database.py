from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool
from redis import asyncio as aioredis
from redis.asyncio import Redis

from app.config.config import settings
from app.models.models import Base, User, UserRole


# 根据数据库类型选择引擎配置
if settings.DATABASE_URL.startswith("sqlite"):
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DEBUG,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
else:
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DEBUG,
        poolclass=AsyncAdaptedQueuePool,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
    )

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

redis_client: Optional[Redis] = None


async def get_redis() -> Optional[Redis]:
    global redis_client
    if not settings.REDIS_ENABLED:
        return None
    if redis_client is None:
        try:
            redis_client = aioredis.Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
        except Exception:
            redis_client = None
    return redis_client


async def close_redis():
    global redis_client
    if redis_client:
        try:
            await redis_client.close()
        except Exception:
            pass
        redis_client = None


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with get_db() as session:
        yield session


async def init_database():
    from app.security import hash_password
    async with engine.begin() as conn:
        # 生产结构由 Alembic 管理；本地 profile 允许首次启动时一次性初始化。
        if settings.ENVIRONMENT.lower() == "local" or settings.DATABASE_URL.startswith("sqlite"):
            await conn.run_sync(Base.metadata.create_all)
        if settings.DATABASE_URL.startswith("sqlite"):
            await conn.execute(__import__('sqlalchemy').text("PRAGMA foreign_keys=ON"))

        # 第 2 步的最小兼容升级：create_all 不会为已有 SQLite 表补列。
        # 第 3 步将由 Alembic 接管完整迁移。
        if settings.DATABASE_URL.startswith("sqlite"):
            from sqlalchemy import text
            result = await conn.execute(text("PRAGMA table_info(users)"))
            columns = {row[1] for row in result.fetchall()}
            additions = {
                "role": "VARCHAR(20) DEFAULT 'user'",
                "password_hash": "VARCHAR(255)",
                "token_version": "INTEGER DEFAULT 0",
                "disabled_at": "DATETIME",
            }
            for name, definition in additions.items():
                if name not in columns:
                    await conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))
            # 本地开发库可能由第 1/2 步创建，补齐第 3 步新增字段。
            result = await conn.execute(text("PRAGMA table_info(tickets)"))
            ticket_columns = {row[1] for row in result.fetchall()}
            for name, definition in {
                "session_id": "VARCHAR(36)", "resolution": "TEXT", "idempotency_key": "VARCHAR(128)"
            }.items():
                if name not in ticket_columns:
                    await conn.execute(text(f"ALTER TABLE tickets ADD COLUMN {name} {definition}"))
            # 第 6 步可观测性兼容升级：为既有本地库补齐请求 Trace/Span 与评测元数据。
            for table, additions in {
                "agent_traces": {
                    "request_trace_id": "VARCHAR(100)", "span_id": "VARCHAR(100)",
                    "model_name": "VARCHAR(100)", "prompt_version": "VARCHAR(50)",
                },
                "evaluation_results": {
                    "trace_id": "VARCHAR(100)", "intent": "VARCHAR(50)", "evaluator": "VARCHAR(50)",
                    "model_name": "VARCHAR(100)", "prompt_version": "VARCHAR(50)",
                    "latency_ms": "INTEGER", "token_usage": "JSON",
                    "user_query": "TEXT", "response": "TEXT", "categories": "JSON",
                    "processed_at": "DATETIME", "processing_action": "VARCHAR(50)", "processing_note": "TEXT",
                },
            }.items():
                result = await conn.execute(text(f"PRAGMA table_info({table})"))
                table_columns = {row[1] for row in result.fetchall()}
                for name, definition in additions.items():
                    if name not in table_columns:
                        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))

    # 创建默认用户（如果不存在）
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(User))
        users = result.scalars().all()
        if not users and settings.AUTH_DEMO_ACCOUNTS_ENABLED:
            default_users = [
                User(id=1, username='user001', nickname='普通用户', level='normal', role=UserRole.USER.value, password_hash=hash_password(settings.DEMO_USER_PASSWORD)),
                User(id=2, username='user002', nickname='VIP用户', level='vip', role=UserRole.USER.value, password_hash=hash_password(settings.DEMO_USER_PASSWORD)),
                User(id=999, username='admin', nickname='管理员', level='enterprise', role=UserRole.ADMIN.value, password_hash=hash_password(settings.DEMO_ADMIN_PASSWORD)),
            ]
            session.add_all(default_users)
            await session.commit()
        elif settings.AUTH_DEMO_ACCOUNTS_ENABLED:
            # 为第 1 步遗留的无密码演示用户补充哈希，并修正管理员角色。
            changed = False
            for user in users:
                if not user.password_hash:
                    user.password_hash = hash_password(settings.DEMO_ADMIN_PASSWORD if user.username == "admin" else settings.DEMO_USER_PASSWORD)
                    changed = True
                if user.username == "admin" and user.role != UserRole.ADMIN.value:
                    user.role = UserRole.ADMIN.value
                    changed = True
                if not user.role:
                    user.role = UserRole.USER.value
                    changed = True
                if user.token_version is None:
                    user.token_version = 0
                    changed = True
            if changed:
                await session.commit()
