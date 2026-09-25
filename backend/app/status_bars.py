from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import APIConfig, CheckRecord, ProbeObservation
from backend.app.repository import format_target, today_availability
from backend.app.time_utils import coerce_aware_utc, utc_now


@dataclass(frozen=True, slots=True)
class StatusWindowSpec:
    key: str
    label: str
    total_minutes: int
    bucket_minutes: int

    @property
    def bucket_count(self) -> int:
        return self.total_minutes // self.bucket_minutes


@dataclass(slots=True)
class StatusBucketData:
    start_at: datetime
    end_at: datetime
    state: str
    ok_count: int
    down_count: int
    total_count: int
    timeout: bool = False
    timeout_count: int = 0


@dataclass(slots=True)
class StatusWindowData:
    key: str
    label: str
    bucket_minutes: int
    buckets: list[StatusBucketData]
    latency_points: list[dict] = field(default_factory=list)


@dataclass(slots=True)
class ConfigStatusBarsData:
    config_id: int
    config_name: str
    target: str
    model_name: str
    status: str
    last_code: str | None
    success_rate: float
    windows: list[StatusWindowData] = field(default_factory=list)


DEFAULT_STATUS_WINDOWS: tuple[StatusWindowSpec, ...] = (
    StatusWindowSpec("30m", "最近30分钟", 30, 1),
    StatusWindowSpec("5h", "最近5小时", 300, 10),
    StatusWindowSpec("24h", "最近24小时", 1440, 60),
)


def build_status_bars(
    session: Session,
    configs: list[APIConfig],
    timezone_name: str,
    *,
    now: datetime | None = None,
    windows: tuple[StatusWindowSpec, ...] = DEFAULT_STATUS_WINDOWS,
) -> list[ConfigStatusBarsData]:
    if not configs:
        return []

    current = coerce_aware_utc(now or utc_now())
    max_minutes = max(window.total_minutes for window in windows)
    since = current - timedelta(minutes=max_minutes)
    config_ids = [config.id for config in configs]
    rows = session.scalars(
        select(CheckRecord)
        .where(CheckRecord.api_config_id.in_(config_ids))
        .where(CheckRecord.scheduled.is_(True))
        .where(CheckRecord.checked_at >= since)
    ).all()

    rows_by_config: dict[int, list[CheckRecord]] = {config.id: [] for config in configs}
    for row in rows:
        rows_by_config.setdefault(row.api_config_id, []).append(row)
    observations = session.scalars(select(ProbeObservation).where(
        ProbeObservation.api_config_id.in_(config_ids), ProbeObservation.checked_at >= since,
    )).all()
    for row in observations:
        rows_by_config.setdefault(row.api_config_id, []).append(row)

    return [
        _build_config_status_bars(
            session,
            config,
            rows_by_config.get(config.id, []),
            timezone_name,
            current,
            windows,
        )
        for config in configs
    ]


def _build_config_status_bars(
    session: Session,
    config: APIConfig,
    records: list[CheckRecord],
    timezone_name: str,
    now: datetime,
    windows: tuple[StatusWindowSpec, ...],
) -> ConfigStatusBarsData:
    config_data = ConfigStatusBarsData(
        config_id=config.id,
        config_name=config.name,
        target=format_target(config.target_type, config.target_id),
        model_name=config.model_name,
        status=config.status,
        last_code=config.last_code,
        success_rate=today_availability(session, config.id, timezone_name),
    )
    for spec in windows:
        config_data.windows.append(_build_window(spec, records, now))
    return config_data


def _build_window(spec: StatusWindowSpec, records: list[CheckRecord], now: datetime) -> StatusWindowData:
    bucket_seconds = spec.bucket_minutes * 60
    start_at = now - timedelta(minutes=spec.total_minutes)
    counters = [{"ok": 0, "down": 0, "timeout": 0} for _ in range(spec.bucket_count)]
    latency_points = []

    for record in records:
        checked_at = coerce_aware_utc(record.checked_at)
        if checked_at < start_at or checked_at > now:
            continue
        offset = (checked_at - start_at).total_seconds()
        index = min(spec.bucket_count - 1, int(offset // bucket_seconds))
        if record.latency_ms is not None:
            latency_points.append({"at": checked_at, "latency_ms": record.latency_ms,
                                   "model_switched": bool(getattr(record, "model_switched", False)), "code": record.code})
        if isinstance(record, ProbeObservation):
            counters[index]["timeout"] += 1
        elif record.status == "ok":
            counters[index]["ok"] += 1
        else:
            counters[index]["down"] += 1

    buckets: list[StatusBucketData] = []
    for index, counts in enumerate(counters):
        bucket_start = start_at + timedelta(seconds=index * bucket_seconds)
        bucket_end = bucket_start + timedelta(seconds=bucket_seconds)
        ok_count = counts["ok"]
        down_count = counts["down"]
        total_count = ok_count + down_count
        buckets.append(
            StatusBucketData(
                start_at=bucket_start,
                end_at=bucket_end,
                state=_bucket_state(ok_count, down_count),
                ok_count=ok_count,
                down_count=down_count,
                total_count=total_count,
                timeout=counts["timeout"] > 0,
                timeout_count=counts["timeout"],
            )
        )
    return StatusWindowData(
        key=spec.key,
        label=spec.label,
        bucket_minutes=spec.bucket_minutes,
        buckets=buckets,
        latency_points=sorted(latency_points, key=lambda point: point["at"]),
    )


def should_render_timeout(window: StatusWindowData) -> bool:
    if window.bucket_minutes <= 1:
        return True
    timeouts = sum(bucket.timeout_count for bucket in window.buckets)
    total = sum(bucket.total_count for bucket in window.buckets) + timeouts
    return total > 0 and timeouts / total > 0.4


def _bucket_state(ok_count: int, down_count: int) -> str:
    if ok_count + down_count == 0:
        return "unknown"
    if down_count == 0:
        return "ok"
    if ok_count > 0:
        return "partial"
    return "down"
