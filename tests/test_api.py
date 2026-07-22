from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.api import config_history, get_command_settings, get_sub2_sentiment, onebot_webhook, update_command_setting, update_config
from backend.app.crypto import SecretBox
from backend.app.models import APIConfig, Base, CheckRecord
from backend.app.schemas import APIConfigUpdate, CommandSettingUpdate
from backend.app.settings import Settings
from backend.app.sub2_sentiment import record_sentiment_vote


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def add_config(session, secret_box, name: str):
    config = APIConfig(
        name=name,
        target_type="group",
        target_id="123",
        base_url="https://example.com/v1",
        api_key_encrypted=secret_box.encrypt("sk-test"),
        model_name="gpt-test",
        enabled=True,
    )
    session.add(config)
    session.commit()
    return config


def test_update_config_renames_api_config():
    session = make_session()
    secret_box = SecretBox("test-key")
    add_config(session, secret_box, "old-name")

    result = update_config(
        "old-name",
        APIConfigUpdate(name="new-name", model_name="gpt-new"),
        session,
        Settings(),
        secret_box,
    )

    assert result.name == "new-name"
    assert result.model_name == "gpt-new"
    assert session.scalar(select(APIConfig).where(APIConfig.name == "old-name")) is None
    assert session.scalar(select(APIConfig).where(APIConfig.name == "new-name")) is not None


def test_update_config_accepts_multi_target():
    session = make_session()
    secret_box = SecretBox("test-key")
    add_config(session, secret_box, "multi-target-api")

    result = update_config(
        "multi-target-api",
        APIConfigUpdate(target="G123&P456"),
        session,
        Settings(),
        secret_box,
    )

    assert result.target_type == "multi"
    assert result.target_id == "G123&P456"
    assert result.target == "G123&P456"


def test_update_config_rejects_duplicate_name():

    session = make_session()
    secret_box = SecretBox("test-key")
    add_config(session, secret_box, "first")
    add_config(session, secret_box, "second")

    with pytest.raises(HTTPException) as exc_info:
        update_config(
            "first",
            APIConfigUpdate(name="second"),
            session,
            Settings(),
            secret_box,
        )

    assert exc_info.value.status_code == 409


def test_config_history_returns_latest_sixty_records():
    session = make_session()
    secret_box = SecretBox("test-key")
    config = add_config(session, secret_box, "history-api")
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(75):
        session.add(
            CheckRecord(
                api_config_id=config.id,
                checked_at=base_time + timedelta(minutes=index),
                status="available",
                code=f"code-{index}",
                scheduled=True,
            )
        )
    session.commit()

    rows = config_history("history-api", session)

    assert len(rows) == 60
    assert rows[0].code == "code-74"
    assert rows[-1].code == "code-15"


class FakeRequest:
    async def body(self) -> bytes:
        return b'{"post_type":"message","message":"/status"}'


@pytest.mark.asyncio
async def test_onebot_http_webhook_is_ignored():
    result = await onebot_webhook(FakeRequest())

    assert result["status"] == "ignored"


def test_command_setting_aliases_roundtrip():
    session = make_session()

    updated = update_command_setting(
        "status",
        CommandSettingUpdate(aliases=["状态"]),
        session,
    )

    assert updated.command == "/status"
    assert updated.aliases == ["状态"]
    rows = get_command_settings(session)
    status_row = next(item for item in rows if item.command == "/status")
    assert status_row.aliases == ["状态"]


def test_sub2_sentiment_api_returns_global_summary():
    session = make_session()
    record_sentiment_vote(session, "10001", "up", "group", "123")
    record_sentiment_vote(session, "10002", "down", "group", "456")

    result = get_sub2_sentiment(session)

    assert result.total_count == 2
    assert result.up_percent == 50.0
    assert result.down_percent == 50.0
