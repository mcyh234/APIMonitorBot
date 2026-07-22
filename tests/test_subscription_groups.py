from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.models import Base, Sub2Config, Sub2RateHistory
from backend.app.sub2_rates import Sub2RateHistoryPoint, Sub2StoredRate, best_subscription_groups, daily_rate_candles, sync_sub2_rates
from backend.app.sub2api import Sub2ChannelRateSnapshot


def _rate(name: str, multiplier: float) -> Sub2StoredRate:
    return Sub2StoredRate(
        platform="openai",
        group_key=name,
        group_name=name,
        rate_multiplier=multiplier,
        last_seen_at=datetime.now(timezone.utc),
        history=(),
    )


def test_best_subscription_groups_selects_lowest_keyword_match():
    result = best_subscription_groups(
        [_rate("Plus 正价", 0.2), _rate("Plus 特惠", 0.06), _rate("Pro", 0.12)]
    )

    assert [(item.category, item.group_name, item.rate_multiplier) for item in result] == [
        ("plus", "Plus 特惠", 0.06),
        ("pro", "Pro", 0.12),
    ]


def test_daily_candles_use_shanghai_day_and_keep_missing_days_empty():
    points = [
        Sub2RateHistoryPoint(datetime(2026, 7, 20, 15, 30, tzinfo=timezone.utc), 0.10),
        Sub2RateHistoryPoint(datetime(2026, 7, 20, 15, 50, tzinfo=timezone.utc), 0.20),
        Sub2RateHistoryPoint(datetime(2026, 7, 20, 16, 10, tzinfo=timezone.utc), 0.15),
        Sub2RateHistoryPoint(datetime(2026, 7, 20, 16, 20, tzinfo=timezone.utc), 0.12),
    ]

    candles = daily_rate_candles(
        points,
        at=datetime(2026, 7, 22, 4, 0, tzinfo=timezone.utc),
    )

    assert [item.date.isoformat() for item in candles] == ["2026-07-20", "2026-07-21"]
    assert (candles[0].open, candles[0].high, candles[0].low, candles[0].close) == (0.10, 0.20, 0.10, 0.20)
    assert (candles[1].open, candles[1].high, candles[1].low, candles[1].close) == (0.15, 0.15, 0.12, 0.12)


def test_sync_records_one_daily_baseline_plus_intraday_changes():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    config = Sub2Config(
        name="prices",
        target_type="group",
        target_id="123",
        base_url="https://example.com",
        email="user@example.com",
        password_encrypted="encrypted",
    )
    session.add(config)
    session.commit()

    def snapshot(rate: float) -> list[Sub2ChannelRateSnapshot]:
        return [Sub2ChannelRateSnapshot("openai", "plus", "Plus", rate)]

    sync_sub2_rates(session, config, snapshot(0.10), at=datetime(2026, 7, 20, 2, 0, tzinfo=timezone.utc))
    sync_sub2_rates(session, config, snapshot(0.10), at=datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc))
    sync_sub2_rates(session, config, snapshot(0.20), at=datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc))
    sync_sub2_rates(session, config, snapshot(0.20), at=datetime(2026, 7, 21, 2, 0, tzinfo=timezone.utc))
    sync_sub2_rates(session, config, snapshot(0.20), at=datetime(2026, 7, 21, 3, 0, tzinfo=timezone.utc))

    rows = session.scalars(select(Sub2RateHistory).order_by(Sub2RateHistory.recorded_at)).all()
    assert [row.rate_multiplier for row in rows] == [0.10, 0.20, 0.20]
