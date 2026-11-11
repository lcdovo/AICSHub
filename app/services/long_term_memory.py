"""Deterministic, explicit communication preferences with SQL source validation."""
from datetime import timedelta
import re
import uuid

from sqlalchemy import select

from app.models.models import Message, MessageRole, Session, UserMemoryEntry
from app.utils.utc_time import utc_now_db

POLICY = "explicit-communication-preferences-v1"


def preference_change(text: str) -> tuple[str, str | None] | None:
    """Only whole-message explicit preferences; quoted/free-form instructions are excluded."""
    text = text.strip().rstrip("。！! ")
    if re.fullmatch(r"(?:请)?(?:忘记|删除|撤回)我的(?:回复|沟通|回答)偏好", text):
        return "all", None
    match = re.fullmatch(r"(?:以后|今后)(?:请)?(?:用|使用)(简体中文|繁体中文|中文|英文|英语)(?:回复|回答)(?:我)?", text)
    if match:
        return "language", match.group(1)
    match = re.fullmatch(r"(?:以后|今后)(?:请)?(?:回复|回答)(?:我)?(?:请)?(简洁一点|详细一点|简洁|详细)", text)
    if match:
        return "verbosity", match.group(1)
    return None


async def record_preference(db, *, user_id: int, session_id: str, message_id: int) -> None:
    source = await db.scalar(select(Message).join(Session, Message.session_id == Session.id).where(
        Message.id == message_id, Message.session_id == session_id, Message.role == MessageRole.USER,
        Session.user_id == user_id, Session.deleted_at.is_(None)))
    change = preference_change(source.content) if source else None
    if not change:
        return
    # Lock the owner to serialize changes across sessions, including first insertion.
    from app.models.models import User
    await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    key, value = change
    statement = select(UserMemoryEntry).where(UserMemoryEntry.user_id == user_id)
    if key != "all":
        statement = statement.where(UserMemoryEntry.memory_key == key)
    entries = list((await db.scalars(statement.with_for_update())).all())
    now = utc_now_db()
    for entry in entries:
        # Delayed/replayed older runs cannot undo a newer correction or revocation.
        if entry.source_message_id >= message_id:
            continue
        entry.source_session_id, entry.source_message_id = session_id, message_id
        entry.updated_at, entry.expires_at = now, now + timedelta(days=180)
        entry.revoked_at = now if value is None else None
        if value is not None:
            entry.content = value
    missing_keys = ({"language", "verbosity"} if key == "all" else {key}) - {e.memory_key for e in entries}
    for missing in sorted(missing_keys):
        db.add(UserMemoryEntry(id=str(uuid.uuid4()), user_id=user_id, memory_key=missing, content=value or "",
            source_session_id=session_id, source_message_id=message_id, updated_at=now,
            revoked_at=now if value is None else None, expires_at=now + timedelta(days=180)))


async def load_preferences(db, user_id: int) -> list[dict]:
    rows = (await db.execute(select(UserMemoryEntry, Message.content).join(
        Session, Session.id == UserMemoryEntry.source_session_id).join(
        Message, Message.id == UserMemoryEntry.source_message_id).where(
        UserMemoryEntry.user_id == user_id, Session.user_id == user_id,
        Session.deleted_at.is_(None), Message.session_id == Session.id,
        Message.role == MessageRole.USER, UserMemoryEntry.revoked_at.is_(None),
        UserMemoryEntry.expires_at > utc_now_db()).order_by(UserMemoryEntry.memory_key))).all()
    return [{"key": row.memory_key, "content": row.content, "message_id": row.source_message_id,
             "session_id": row.source_session_id, "updated_at": row.updated_at.isoformat(),
             "expires_at": row.expires_at.isoformat(), "source_type": POLICY,
             "trusted_as_current_evidence": False, "grants_authorization": False}
            for row, source_content in rows if preference_change_source(row)
            and preference_change(source_content) == (row.memory_key, row.content)]


def preference_change_source(row) -> bool:
    # Stored values remain constrained even if legacy/manual data is present.
    return ((row.memory_key == "language" and row.content in {"简体中文", "繁体中文", "中文", "英文", "英语"})
            or (row.memory_key == "verbosity" and row.content in {"简洁", "详细", "简洁一点", "详细一点"}))
