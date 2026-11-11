from datetime import datetime, timedelta, timezone

from app.utils.utc_time import as_utc, utc_from_timestamp, utc_now, utc_now_db


def test_runtime_clock_returns_aware_utc_time():
    value = utc_now()

    assert value.tzinfo is timezone.utc


def test_database_clock_preserves_naive_utc_contract():
    before = utc_now()
    value = utc_now_db()
    after = utc_now()

    assert value.tzinfo is None
    assert before.replace(tzinfo=None) <= value <= after.replace(tzinfo=None)


def test_as_utc_normalizes_legacy_and_offset_values():
    legacy = datetime(2026, 11, 11, 12, 0)
    offset_value = datetime(2026, 11, 11, 14, 0, tzinfo=timezone(timedelta(hours=2)))

    assert as_utc(legacy) == datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    assert as_utc(offset_value) == datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def test_unix_timestamp_conversion_is_aware_utc():
    value = utc_from_timestamp(0)

    assert value == datetime(1970, 1, 1, tzinfo=timezone.utc)
