from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Sub2ChannelRate, Sub2Config, Sub2RateHistory
from backend.app.sub2api import Sub2ChannelRateSnapshot
from backend.app.time_utils import utc_now


@dataclass(frozen=True, slots=True)
class Sub2RateChange:
    platform: str
    group_key: str
    group_name: str
    old_rate: float
    new_rate: float | None
    change_type: str = "rate"

    @property
    def identity(self) -> tuple[str, str]:
        return (self.platform, self.group_key)

    @property
    def is_deleted(self) -> bool:
        return self.change_type == "deleted"


@dataclass(frozen=True, slots=True)
class Sub2RateHistoryPoint:
    recorded_at: datetime
    rate_multiplier: float


@dataclass(frozen=True, slots=True)
class Sub2StoredRate:
    platform: str
    group_key: str
    group_name: str
    rate_multiplier: float
    last_seen_at: datetime
    history: tuple[Sub2RateHistoryPoint, ...]

    @property
    def identity(self) -> tuple[str, str]:
        return (self.platform, self.group_key)

    @property
    def previous_rate(self) -> float | None:
        return previous_distinct_rate(self.history, self.rate_multiplier)

    @property
    def change_percent(self) -> float | None:
        previous = self.previous_rate
        if previous is None or math.isclose(previous, 0, rel_tol=0, abs_tol=1e-12):
            return None
        return (self.rate_multiplier - previous) / previous * 100


def sync_sub2_rates(
    session: Session,
    config: Sub2Config,
    rates: list[Sub2ChannelRateSnapshot],
) -> list[Sub2RateChange]:
    now = utc_now()
    existing = {
        (row.platform, row.group_key): row
        for row in session.scalars(
            select(Sub2ChannelRate).where(Sub2ChannelRate.sub2_config_id == config.id)
        ).all()
    }
    history_keys = {
        (row.platform, row.group_key)
        for row in session.scalars(
            select(Sub2RateHistory).where(Sub2RateHistory.sub2_config_id == config.id)
        ).all()
    }
    next_keys = {rate.identity for rate in rates}
    changes: list[Sub2RateChange] = []
    for rate in rates:
        row = existing.get(rate.identity)
        if row is None:
            session.add(
                Sub2ChannelRate(
                    sub2_config_id=config.id,
                    platform=rate.platform,
                    group_key=rate.group_key,
                    group_name=rate.group_name,
                    rate_multiplier=rate.rate_multiplier,
                    last_seen_at=now,
                )
            )
            _append_history(session, config.id, rate, now)
            continue
        if rate.identity not in history_keys:
            _append_history(
                session,
                config.id,
                Sub2ChannelRateSnapshot(
                    platform=row.platform,
                    group_key=row.group_key,
                    group_name=row.group_name,
                    rate_multiplier=row.rate_multiplier,
                ),
                row.last_seen_at or now,
            )
        if not math.isclose(row.rate_multiplier, rate.rate_multiplier, rel_tol=0, abs_tol=1e-12):
            changes.append(
                Sub2RateChange(
                    platform=rate.platform,
                    group_key=rate.group_key,
                    group_name=rate.group_name,
                    old_rate=row.rate_multiplier,
                    new_rate=rate.rate_multiplier,
                )
            )
            _append_history(session, config.id, rate, now)
        row.group_name = rate.group_name
        row.rate_multiplier = rate.rate_multiplier
        row.last_seen_at = now

    for key, row in existing.items():
        if key not in next_keys:
            changes.append(
                Sub2RateChange(
                    platform=row.platform,
                    group_key=row.group_key,
                    group_name=row.group_name,
                    old_rate=row.rate_multiplier,
                    new_rate=None,
                    change_type="deleted",
                )
            )
            session.delete(row)
    session.commit()
    return changes


def stored_sub2_rate_views(session: Session, config: Sub2Config) -> list[Sub2StoredRate]:
    rows = session.scalars(
        select(Sub2ChannelRate)
        .where(Sub2ChannelRate.sub2_config_id == config.id)
        .order_by(Sub2ChannelRate.platform, Sub2ChannelRate.group_name)
    ).all()
    history_rows = session.scalars(
        select(Sub2RateHistory)
        .where(Sub2RateHistory.sub2_config_id == config.id)
        .order_by(Sub2RateHistory.recorded_at.asc(), Sub2RateHistory.id.asc())
    ).all()
    history_by_key: dict[tuple[str, str], list[Sub2RateHistoryPoint]] = {}
    for row in history_rows:
        history_by_key.setdefault((row.platform, row.group_key), []).append(
            Sub2RateHistoryPoint(row.recorded_at, row.rate_multiplier)
        )

    views: list[Sub2StoredRate] = []
    for row in rows:
        points = list(history_by_key.get((row.platform, row.group_key), []))
        if not points:
            points.append(Sub2RateHistoryPoint(row.last_seen_at, row.rate_multiplier))
        elif not math.isclose(points[-1].rate_multiplier, row.rate_multiplier, rel_tol=0, abs_tol=1e-12):
            points.append(Sub2RateHistoryPoint(row.last_seen_at, row.rate_multiplier))
        views.append(
            Sub2StoredRate(
                platform=row.platform,
                group_key=row.group_key,
                group_name=row.group_name,
                rate_multiplier=row.rate_multiplier,
                last_seen_at=row.last_seen_at,
                history=tuple(points),
            )
        )
    return views


def stored_sub2_rates(session: Session, config: Sub2Config) -> list[Sub2ChannelRateSnapshot]:
    rows = session.scalars(
        select(Sub2ChannelRate)
        .where(Sub2ChannelRate.sub2_config_id == config.id)
        .order_by(Sub2ChannelRate.platform, Sub2ChannelRate.group_name)
    ).all()
    return [
        Sub2ChannelRateSnapshot(
            platform=row.platform,
            group_key=row.group_key,
            group_name=row.group_name,
            rate_multiplier=row.rate_multiplier,
        )
        for row in rows
    ]


def previous_distinct_rate(
    history: tuple[Sub2RateHistoryPoint, ...] | list[Sub2RateHistoryPoint],
    current_rate: float,
) -> float | None:
    for point in reversed(history):
        if not math.isclose(point.rate_multiplier, current_rate, rel_tol=0, abs_tol=1e-12):
            return point.rate_multiplier
    return None


def _append_history(
    session: Session,
    config_id: int,
    rate: Sub2ChannelRateSnapshot,
    recorded_at: datetime,
) -> None:
    session.add(
        Sub2RateHistory(
            sub2_config_id=config_id,
            recorded_at=recorded_at,
            platform=rate.platform,
            group_key=rate.group_key,
            group_name=rate.group_name,
            rate_multiplier=rate.rate_multiplier,
        )
    )
