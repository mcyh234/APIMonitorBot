from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.models import Base, Sub2SentimentVote
from backend.app.sub2_sentiment import record_sentiment_vote, sentiment_date, sentiment_summary


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def test_sentiment_uses_shanghai_calendar_day():
    assert sentiment_date(datetime(2026, 7, 20, 15, 59, tzinfo=timezone.utc)).isoformat() == "2026-07-20"
    assert sentiment_date(datetime(2026, 7, 20, 16, 0, tzinfo=timezone.utc)).isoformat() == "2026-07-21"


def test_vote_is_global_per_user_day_and_can_change():
    session = make_session()
    at = datetime(2026, 7, 21, 4, 0, tzinfo=timezone.utc)

    first = record_sentiment_vote(session, "10001", "up", "group", "111", at=at)
    same = record_sentiment_vote(session, "10001", "up", "group", "222", at=at)
    changed = record_sentiment_vote(session, "10001", "down", "group", "222", at=at)
    record_sentiment_vote(session, "10002", "up", "group", "111", at=at)

    assert first.action == "created"
    assert same.action == "unchanged"
    assert changed.action == "changed"
    assert session.scalars(select(Sub2SentimentVote)).all().__len__() == 2
    summary = sentiment_summary(session, at=at)
    assert (summary.up_count, summary.down_count, summary.total_count) == (1, 1, 2)
    assert (summary.up_percent, summary.down_percent) == (50.0, 50.0)


def test_user_can_vote_again_on_next_shanghai_day():
    session = make_session()
    before_midnight = datetime(2026, 7, 20, 15, 59, tzinfo=timezone.utc)
    after_midnight = datetime(2026, 7, 20, 16, 0, tzinfo=timezone.utc)

    record_sentiment_vote(session, "10001", "up", "group", "111", at=before_midnight)
    record_sentiment_vote(session, "10001", "down", "group", "111", at=after_midnight)

    assert len(session.scalars(select(Sub2SentimentVote)).all()) == 2
