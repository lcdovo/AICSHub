"""SQL任务入队契约；消费者由独立worker切片提供。"""
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import BackgroundJob


def enqueue(db: AsyncSession, kind: str, resource_id: str, owner_id: int | None, event_key: str) -> None:
    db.add(BackgroundJob(id=str(uuid.uuid4()), event_key=event_key, kind=kind, resource_id=resource_id, user_id=owner_id))
