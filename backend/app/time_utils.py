from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def coerce_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def api_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return coerce_aware_utc(value)


def local_day_start_utc(tz_name: str, at: datetime | None = None) -> datetime:
    tz = ZoneInfo(tz_name)
    current = at or utc_now()
    local = coerce_aware_utc(current).astimezone(tz)
    start_local = datetime.combine(local.date(), time.min, tzinfo=tz)
    return start_local.astimezone(UTC)
