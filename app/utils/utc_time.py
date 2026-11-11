"""Shared UTC clock helpers for runtime and persistence timestamps."""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return an aware UTC timestamp for API, token, and serialized values."""
    return datetime.now(timezone.utc)


def utc_now_db() -> datetime:
    """Return UTC without tzinfo for legacy ``DateTime(timezone=False)`` columns.

    Database timestamps remain naive UTC until a separately planned schema/data
    migration can make every writer and reader timezone-aware together.
    """
    return utc_now().replace(tzinfo=None)


def utc_from_timestamp(timestamp: float) -> datetime:
    """Convert a Unix timestamp to an aware UTC datetime."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Interpret legacy naive datetimes as UTC and normalize aware values to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
