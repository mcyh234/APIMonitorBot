from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models import Sub2SentimentVote
from backend.app.time_utils import coerce_aware_utc, utc_now


SENTIMENT_TIMEZONE = "Asia/Shanghai"
VALID_DIRECTIONS = {"up", "down"}


@dataclass(frozen=True, slots=True)
class Sub2SentimentSummary:
    date: date
    up_count: int
    down_count: int

    @property
    def total_count(self) -> int:
        return self.up_count + self.down_count

    @property
    def up_percent(self) -> float:
        return self.up_count / self.total_count * 100 if self.total_count else 0.0

    @property
    def down_percent(self) -> float:
        return self.down_count / self.total_count * 100 if self.total_count else 0.0


@dataclass(frozen=True, slots=True)
class Sub2VoteResult:
    action: str
    summary: Sub2SentimentSummary


def sentiment_date(at: datetime | None = None) -> date:
    current = coerce_aware_utc(at or utc_now())
    return current.astimezone(ZoneInfo(SENTIMENT_TIMEZONE)).date()


def sentiment_summary(
    session: Session,
    *,
    at: datetime | None = None,
) -> Sub2SentimentSummary:
    day = sentiment_date(at)
    rows = session.execute(
        select(Sub2SentimentVote.direction, func.count(Sub2SentimentVote.id))
        .where(Sub2SentimentVote.vote_date == day)
        .group_by(Sub2SentimentVote.direction)
    ).all()
    counts = {str(direction): int(count) for direction, count in rows}
    return Sub2SentimentSummary(day, counts.get("up", 0), counts.get("down", 0))


def record_sentiment_vote(
    session: Session,
    user_id: str,
    direction: str,
    source_type: str,
    source_id: str,
    *,
    at: datetime | None = None,
) -> Sub2VoteResult:
    clean_direction = direction.strip().lower()
    if clean_direction not in VALID_DIRECTIONS:
        raise ValueError("投票方向必须是 up 或 down。")
    now = at or utc_now()
    day = sentiment_date(now)
    row = session.scalar(
        select(Sub2SentimentVote)
        .where(Sub2SentimentVote.user_id == str(user_id))
        .where(Sub2SentimentVote.vote_date == day)
    )
    if row is None:
        row = Sub2SentimentVote(
            user_id=str(user_id),
            vote_date=day,
            direction=clean_direction,
            source_type=source_type,
            source_id=str(source_id),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        action = "created"
    elif row.direction == clean_direction:
        action = "unchanged"
    else:
        row.direction = clean_direction
        row.source_type = source_type
        row.source_id = str(source_id)
        row.updated_at = now
        action = "changed"
    session.commit()
    return Sub2VoteResult(action, sentiment_summary(session, at=now))
