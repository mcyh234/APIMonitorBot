from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Sub2ChannelRate, Sub2Config, Sub2RateHistory
from backend.app.sub2api import Sub2ChannelRateSnapshot
from backend.app.time_utils import coerce_aware_utc, utc_now


SUB2_CANDLE_TIMEZONE = "Asia/Shanghai"
SUB2_CANDLE_DAYS = 30


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
class Sub2DailyCandle:
    date: date
    open: float
    high: float
    low: float
    close: float


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


@dataclass(frozen=True, slots=True)
class SubscriptionBestGroup:
    category: str
    label: str
    group_name: str
    platform: str
    rate_multiplier: float


_SUBSCRIPTION_CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("plus", "最低 Plus", ("plus", "plus会员", "plus订阅", "plus号")),
    ("pro", "最低 Pro", ("pro", "专业版", "pro会员", "pro号")),
    ("team", "最低 Team", ("team", "团队")),
    ("max", "最低 Max", ("max",)),
    ("claude", "最低 Claude", ("claude",)),
)


def best_subscription_groups(rates: list[Sub2StoredRate]) -> list[SubscriptionBestGroup]:
    """Choose the lowest multiplier for every subscription keyword category."""
    result: list[SubscriptionBestGroup] = []
    for category, label, keywords in _SUBSCRIPTION_CATEGORIES:
        matched = [
            rate
            for rate in rates
            if any(keyword in f"{rate.group_name} {rate.group_key} {rate.platform}".casefold() for keyword in keywords)
        ]
        if not matched:
            continue
        lowest = min(matched, key=lambda item: (item.rate_multiplier, item.group_name.casefold()))
        result.append(
            SubscriptionBestGroup(
                category=category,
                label=label,
                group_name=lowest.group_name,
                platform=lowest.platform,
                rate_multiplier=lowest.rate_multiplier,
            )
        )
    return result


def sync_sub2_rates(
    session: Session,
    config: Sub2Config,
    rates: list[Sub2ChannelRateSnapshot],
    *,
    at: datetime | None = None,
) -> list[Sub2RateChange]:
    now = at or utc_now()
    today = _local_date(now)
    existing = {
        (row.platform, row.group_key): row
        for row in session.scalars(
            select(Sub2ChannelRate).where(Sub2ChannelRate.sub2_config_id == config.id)
        ).all()
    }
    history_by_key: dict[tuple[str, str], list[Sub2RateHistory]] = {}
    for history_row in session.scalars(
        select(Sub2RateHistory)
        .where(Sub2RateHistory.sub2_config_id == config.id)
        .order_by(Sub2RateHistory.recorded_at.asc(), Sub2RateHistory.id.asc())
    ).all():
        history_by_key.setdefault((history_row.platform, history_row.group_key), []).append(history_row)
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
        history_rows = history_by_key.get(rate.identity, [])
        if not history_rows:
            baseline_at = row.last_seen_at or now
            history_rows.append(_append_history(
                session,
                config.id,
                Sub2ChannelRateSnapshot(
                    platform=row.platform,
                    group_key=row.group_key,
                    group_name=row.group_name,
                    rate_multiplier=row.rate_multiplier,
                ),
                baseline_at,
            ))
        if not any(_local_date(item.recorded_at) == today for item in history_rows):
            history_rows.append(_append_history(
                session,
                config.id,
                Sub2ChannelRateSnapshot(
                    platform=row.platform,
                    group_key=row.group_key,
                    group_name=row.group_name,
                    rate_multiplier=row.rate_multiplier,
                ),
                now,
            ))
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


def daily_rate_candles(
    history: tuple[Sub2RateHistoryPoint, ...] | list[Sub2RateHistoryPoint],
    *,
    days: int = SUB2_CANDLE_DAYS,
    at: datetime | None = None,
) -> list[Sub2DailyCandle]:
    if days <= 0:
        return []
    today = _local_date(at or utc_now())
    first_day = today - timedelta(days=days - 1)
    grouped: dict[date, list[float]] = {}
    for point in sorted(history, key=lambda item: coerce_aware_utc(item.recorded_at)):
        day = _local_date(point.recorded_at)
        if first_day <= day <= today:
            grouped.setdefault(day, []).append(point.rate_multiplier)
    return [
        Sub2DailyCandle(
            date=day,
            open=values[0],
            high=max(values),
            low=min(values),
            close=values[-1],
        )
        for day, values in sorted(grouped.items())
    ]


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
) -> Sub2RateHistory:
    row = Sub2RateHistory(
            sub2_config_id=config_id,
            recorded_at=recorded_at,
            platform=rate.platform,
            group_key=rate.group_key,
            group_name=rate.group_name,
            rate_multiplier=rate.rate_multiplier,
        )
    session.add(row)
    return row


def _local_date(value: datetime) -> date:
    return coerce_aware_utc(value).astimezone(ZoneInfo(SUB2_CANDLE_TIMEZONE)).date()
