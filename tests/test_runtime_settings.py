from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.crypto import SecretBox
from backend.app.models import Base
from backend.app.runtime_settings import (
    apply_runtime_settings,
    current_monitoring_runtime_settings,
    save_monitoring_runtime_settings,
)
from backend.app.settings import Settings


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def test_monitoring_runtime_settings_persist_and_reload():
    session = make_session()
    settings = Settings()

    current = save_monitoring_runtime_settings(
        session,
        settings,
        night_saver_enabled=True,
        night_saver_start_time="23:30",
        night_saver_end_time="07:15",
        night_saver_interval_minutes=15,
        command_cooldown_minutes=0,
    )

    assert current.night_saver_start_time == "23:30"
    assert current.night_saver_end_time == "07:15"
    assert current.night_saver_interval_minutes == 15
    assert current.command_cooldown_minutes == 0
    assert settings.night_saver_start_hour == 23
    assert settings.night_saver_start_minute == 30
    assert settings.night_saver_interval_seconds == 900
    assert settings.command_check_cooldown_seconds == 0

    reloaded = Settings(
        night_saver_enabled=False,
        night_saver_start_hour=1,
        night_saver_end_hour=2,
        command_check_cooldown_seconds=300,
    )
    apply_runtime_settings(session, reloaded, SecretBox("test-key"))
    persisted = current_monitoring_runtime_settings(reloaded)

    assert persisted == current
