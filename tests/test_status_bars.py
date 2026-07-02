from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.crypto import SecretBox
from backend.app.models import APIConfig, Base, CheckRecord
from backend.app.status_bars import build_status_bars


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def test_status_bars_bucket_states():
    session = make_session()
    secret_box = SecretBox("test-key")
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    config = APIConfig(
        name="cfg-one",
        target_type="group",
        target_id="123456",
        base_url="https://example.com/v1",
        api_key_encrypted=secret_box.encrypt("sk-test"),
        model_name="gpt-test",
        enabled=True,
    )
    session.add(config)
    session.commit()
    session.refresh(config)
    session.add_all(
        [
            CheckRecord(
                api_config_id=config.id,
                checked_at=now - timedelta(minutes=1),
                status="ok",
                code="200",
                scheduled=True,
            ),
            CheckRecord(
                api_config_id=config.id,
                checked_at=now - timedelta(minutes=2),
                status="down",
                code="503",
                scheduled=True,
            ),
        ]
    )
    session.commit()

    bars = build_status_bars(session, [config], "Asia/Shanghai", now=now)

    assert len(bars) == 1
    windows = {window.key: window for window in bars[0].windows}
    assert windows["30m"].buckets[0].state == "unknown"
    assert windows["30m"].buckets[-1].state == "ok"
    assert windows["30m"].buckets[-2].state == "down"
    assert windows["5h"].buckets[-1].state == "partial"
    assert windows["24h"].buckets[-1].state == "partial"
