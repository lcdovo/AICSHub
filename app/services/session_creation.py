"""显式会话创建：数据库归属与审计原子提交，缓存仅初始化一次。"""
import logging
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.memory import session_manager
from app.models.models import OperationAudit, Session, SessionStatus, User
from app.security import ensure_owner_or_admin

logger = logging.getLogger(__name__)


async def create_session(db: AsyncSession, actor: User, owner_id: int) -> Session:
    ensure_owner_or_admin(actor, owner_id)
    owner = await db.get(User, owner_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    actor_id = actor.id
    session = Session(id=str(uuid.uuid4()), user_id=owner_id, status=SessionStatus.ACTIVE)
    db.add(session)
    db.add(OperationAudit(
        operation="create_session", idempotency_key=f"session-created:{session.id}",
        user_id=owner_id, resource_id=session.id,
        request_payload={"actor_id": actor_id, "owner_id": owner_id},
        result_payload={"session_id": session.id},
    ))
    await db.commit()
    await db.refresh(session)
    # Redis不是创建事实来源；提交后初始化失败不能诱使客户端重建已有会话。
    # NX防止迟到的初始化覆盖已完成的聊天，且不写进程内旧快照。
    try:
        await session_manager.initialize_state(session.id, owner_id)
    except Exception:
        logger.warning("Session snapshot initialization unavailable; database session retained")
    return session
