from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.availability import CheckResult, ConnectivityResult
from backend.app.crypto import SecretBox
from backend.app.models import APIConfig, Base, CheckRecord, Sub2ChannelRate, Sub2Config, Sub2RateHistory
from backend.app.monitor import MonitorService, night_saver_active, scheduled_interval_seconds
from backend.app.notifier import NotifyTarget
from backend.app.settings import Settings
from backend.app.sub2api import Sub2ChannelRateSnapshot


class SequenceProbe:
    def __init__(self, results):
        self.results = list(results)

    async def probe(self, base_url: str, api_key: str, model_name: str):
        if self.results:
            return self.results.pop(0)
        return CheckResult(ok=True, code="200")


class FakeNotifier:
    def __init__(self):
        self.messages: list[tuple[NotifyTarget, str]] = []
        self.images: list[tuple[NotifyTarget, str]] = []

    async def send(self, target: NotifyTarget, message: str) -> None:
        self.messages.append((target, message))

    async def send_image(self, target: NotifyTarget, image_bytes: bytes, filename: str) -> None:
        self.images.append((target, filename))


class FakeInternetProbe:
    def __init__(self, result: ConnectivityResult):
        self.result = result
        self.calls = 0

    async def check(self) -> ConnectivityResult:
        self.calls += 1
        return self.result


class FakeSub2Client:
    def __init__(self, rates: list[Sub2ChannelRateSnapshot]):
        self.rates = rates
        self.calls = 0

    async def fetch_rates_with_cached_token(self, session, config, secret_box):
        self.calls += 1
        return list(self.rates)


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def make_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def add_config(session, secret_box, name="cfg", target_type="group", target_id="123"):
    config = APIConfig(
        name=name,
        target_type=target_type,
        target_id=target_id,
        base_url="https://example.com/v1",
        api_key_encrypted=secret_box.encrypt("sk-test"),
        model_name="gpt-test",
        enabled=True,
    )
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def add_sub2_config(session, secret_box, name="sub2", target_type="group", target_id="123"):
    config = Sub2Config(
        name=name,
        target_type=target_type,
        target_id=target_id,
        base_url="https://pool.example.com",
        email="bot@example.com",
        password_encrypted=secret_box.encrypt("password"),
        access_token_encrypted=secret_box.encrypt("access-token"),
        enabled=True,
    )
    session.add(config)
    session.commit()
    session.refresh(config)
    session.add(
        Sub2ChannelRate(
            sub2_config_id=config.id,
            platform="openai",
            group_key="2",
            group_name="OpenAi",
            rate_multiplier=0.1,
        )
    )
    session.commit()
    return config


def test_night_saver_uses_ten_minute_interval_at_night():
    settings = Settings(
        app_timezone="Asia/Shanghai",
        check_interval_seconds=60,
        night_saver_enabled=True,
        night_saver_start_hour=0,
        night_saver_end_hour=8,
        night_saver_interval_seconds=600,
    )
    shanghai_1am = datetime(2026, 6, 30, 17, 0, tzinfo=timezone.utc)
    shanghai_9am = datetime(2026, 7, 1, 1, 0, tzinfo=timezone.utc)

    assert night_saver_active(settings, shanghai_1am) is True
    assert scheduled_interval_seconds(settings, shanghai_1am) == 600
    assert night_saver_active(settings, shanghai_9am) is False
    assert scheduled_interval_seconds(settings, shanghai_9am) == 60


def test_night_saver_skips_scheduled_runs_until_interval_passes():
    settings = Settings(
        app_timezone="Asia/Shanghai",
        check_interval_seconds=60,
        night_saver_enabled=True,
        night_saver_start_hour=0,
        night_saver_end_hour=8,
        night_saver_interval_seconds=600,
    )
    monitor = MonitorService(settings, SecretBox("test-key"), FakeNotifier(), probe=SequenceProbe([]))
    first_run = datetime(2026, 6, 30, 17, 0, tzinfo=timezone.utc)

    assert monitor.should_run_scheduled(first_run) is True
    monitor._last_scheduled_run_at = first_run
    assert monitor.should_run_scheduled(first_run + timedelta(minutes=9, seconds=59)) is False
    assert monitor.should_run_scheduled(first_run + timedelta(minutes=10)) is True
    assert monitor.outage_repeat_checks_for_current_interval(first_run) == 2


@pytest.mark.asyncio
async def test_monitor_outage_followup_and_recovery_notifications():
    session = make_session()
    secret_box = SecretBox("test-key")
    config = add_config(session, secret_box)
    notifier = FakeNotifier()
    down = CheckResult(ok=False, code="500", error="boom")
    ok = CheckResult(ok=True, code="200")
    results = [down, down] * 10 + [ok, ok]
    monitor = MonitorService(
        Settings(check_retry_delay_seconds=0),
        secret_box,
        notifier,
        probe=SequenceProbe(results),
    )

    for _ in range(10):
        await monitor.check_config(session, config, scheduled=True, notify=True)
    assert len(notifier.messages) == 2
    assert "当前出现业务中断" in notifier.messages[0][1]
    assert "10分钟仍未恢复业务" in notifier.messages[1][1]

    await monitor.check_config(session, config, scheduled=True, notify=True)
    await monitor.check_config(session, config, scheduled=True, notify=True)
    assert len(notifier.messages) == 3
    assert "当前服务恢复可用" in notifier.messages[2][1]


@pytest.mark.asyncio
async def test_run_all_scheduled_merges_same_target_outage_notifications():
    session_factory = make_session_factory()
    secret_box = SecretBox("test-key")
    with session_factory() as session:
        add_config(session, secret_box, name="cfg-a", target_id="123")
        add_config(session, secret_box, name="cfg-b", target_id="123")

    notifier = FakeNotifier()
    down = CheckResult(ok=False, code="500", error="boom")
    monitor = MonitorService(
        Settings(check_retry_delay_seconds=0, night_saver_enabled=False),
        secret_box,
        notifier,
        probe=SequenceProbe([down, down, down, down]),
    )

    await monitor.run_all_scheduled(session_factory)

    assert len(notifier.messages) == 1
    target, message = notifier.messages[0]
    assert target.target_type == "group"
    assert target.target_id == "123"
    assert "本轮检测发现多项 API 状态变化" in message
    assert "【cfg-a】" in message
    assert "【cfg-b】" in message
    assert message.count("当前出现业务中断") == 2


@pytest.mark.asyncio
async def test_run_all_scheduled_expands_multi_target_outage_notifications():
    session_factory = make_session_factory()
    secret_box = SecretBox("test-key")
    with session_factory() as session:
        add_config(session, secret_box, name="cfg-multi", target_type="multi", target_id="G123&P456")

    notifier = FakeNotifier()
    down = CheckResult(ok=False, code="500", error="boom")
    monitor = MonitorService(
        Settings(check_retry_delay_seconds=0, night_saver_enabled=False),
        secret_box,
        notifier,
        probe=SequenceProbe([down, down]),
    )

    await monitor.run_all_scheduled(session_factory)

    targets = sorted((target.target_type, target.target_id) for target, _message in notifier.messages)
    assert targets == [("group", "123"), ("private", "456")]
    assert all("【cfg-multi】" in message for _target, message in notifier.messages)

@pytest.mark.asyncio
async def test_run_all_scheduled_sends_sub2_price_change_image():

    session_factory = make_session_factory()
    secret_box = SecretBox("test-key")
    with session_factory() as session:
        add_sub2_config(session, secret_box, name="gptstore", target_id="123")

    notifier = FakeNotifier()
    sub2_client = FakeSub2Client([Sub2ChannelRateSnapshot("openai", "2", "OpenAi", 0.06)])
    monitor = MonitorService(
        Settings(night_saver_enabled=False),
        secret_box,
        notifier,
        probe=SequenceProbe([]),
        sub2_client=sub2_client,
    )

    await monitor.run_all_scheduled(session_factory)

    assert sub2_client.calls == 1
    assert notifier.images == [(NotifyTarget("group", "123"), "sub2-price-change.png")]
    assert len(notifier.messages) == 1
    message_target, message = notifier.messages[0]
    assert message_target == NotifyTarget("group", "123")
    assert "Sub2API 渠道分组发生变化" in message
    assert "OpenAI / OpenAi：0.1x -> 0.06x，下跌 40.0%" in message
    with session_factory() as session:
        row = session.scalar(select(Sub2ChannelRate).where(Sub2ChannelRate.group_key == "2"))
        assert row.rate_multiplier == 0.06
        history = session.scalars(
            select(Sub2RateHistory)
            .where(Sub2RateHistory.sub2_config_id == row.sub2_config_id)
            .where(Sub2RateHistory.group_key == "2")
            .order_by(Sub2RateHistory.recorded_at)
        ).all()
        assert [item.rate_multiplier for item in history] == [0.1, 0.06]


@pytest.mark.asyncio
async def test_run_all_scheduled_sends_sub2_price_change_image_to_multi_targets():
    session_factory = make_session_factory()
    secret_box = SecretBox("test-key")
    with session_factory() as session:
        add_sub2_config(session, secret_box, name="gptstore", target_type="multi", target_id="G123&P456")

    notifier = FakeNotifier()
    sub2_client = FakeSub2Client([Sub2ChannelRateSnapshot("openai", "2", "OpenAi", 0.06)])
    monitor = MonitorService(
        Settings(night_saver_enabled=False),
        secret_box,
        notifier,
        probe=SequenceProbe([]),
        sub2_client=sub2_client,
    )

    await monitor.run_all_scheduled(session_factory)

    image_targets = sorted((target.target_type, target.target_id, filename) for target, filename in notifier.images)
    assert image_targets == [
        ("group", "123", "sub2-price-change.png"),
        ("private", "456", "sub2-price-change.png"),
    ]
    message_targets = sorted((target.target_type, target.target_id) for target, _message in notifier.messages)
    assert message_targets == [("group", "123"), ("private", "456")]
    assert all("OpenAI / OpenAi：0.1x -> 0.06x，下跌 40.0%" in message for _target, message in notifier.messages)

@pytest.mark.asyncio
async def test_run_all_scheduled_notifies_sub2_deleted_group():
    session_factory = make_session_factory()
    secret_box = SecretBox("test-key")
    with session_factory() as session:
        add_sub2_config(session, secret_box, name="gptstore", target_id="123")

    notifier = FakeNotifier()
    sub2_client = FakeSub2Client([])
    monitor = MonitorService(
        Settings(night_saver_enabled=False),
        secret_box,
        notifier,
        probe=SequenceProbe([]),
        sub2_client=sub2_client,
    )

    await monitor.run_all_scheduled(session_factory)

    assert notifier.images == [(NotifyTarget("group", "123"), "sub2-price-change.png")]
    assert len(notifier.messages) == 1
    target, message = notifier.messages[0]
    assert target == NotifyTarget("group", "123")
    assert "OpenAI / OpenAi：分组已删除，最后倍率 0.1x" in message
    with session_factory() as session:
        assert session.scalar(select(Sub2ChannelRate).where(Sub2ChannelRate.group_key == "2")) is None

@pytest.mark.asyncio
async def test_scheduled_timeout_is_ignored_when_google_is_reachable():


    session = make_session()
    secret_box = SecretBox("test-key")
    config = add_config(session, secret_box)
    notifier = FakeNotifier()
    timeout = CheckResult(ok=False, code="TIMEOUT", error="Request timed out.")
    internet_probe = FakeInternetProbe(ConnectivityResult(ok=True, code="204"))
    monitor = MonitorService(
        Settings(check_retry_delay_seconds=0),
        secret_box,
        notifier,
        probe=SequenceProbe([timeout, timeout]),
        internet_probe=internet_probe,
    )

    result = await monitor.check_config(session, config, scheduled=True, notify=True)

    assert result.code == "TIMEOUT"
    assert internet_probe.calls == 1
    assert notifier.messages == []
    assert session.scalar(select(CheckRecord)) is None
    assert config.status == "unknown"
    assert config.failure_checks == 0
    assert config.success_checks == 0


@pytest.mark.asyncio
async def test_scheduled_network_error_is_ignored_without_notifications():
    session = make_session()
    secret_box = SecretBox("test-key")
    config = add_config(session, secret_box)
    notifier = FakeNotifier()
    network_error = CheckResult(ok=False, code="NETWORK_ERROR", error="dns failed")
    internet_probe = FakeInternetProbe(ConnectivityResult(ok=True, code="204"))
    monitor = MonitorService(
        Settings(check_retry_delay_seconds=0),
        secret_box,
        notifier,
        probe=SequenceProbe([network_error, network_error]),
        internet_probe=internet_probe,
    )

    result = await monitor.check_config(session, config, scheduled=True, notify=True)

    assert result.code == "NETWORK_ERROR"
    assert internet_probe.calls == 0
    assert notifier.messages == []
    assert session.scalar(select(CheckRecord)) is None
    assert config.status == "unknown"
    assert config.failure_checks == 0
    assert config.success_checks == 0


@pytest.mark.asyncio
async def test_scheduled_timeout_notifies_default_admin_when_google_is_unreachable():
    session = make_session()
    secret_box = SecretBox("test-key")
    config = add_config(session, secret_box)
    notifier = FakeNotifier()
    timeout = CheckResult(ok=False, code="TIMEOUT", error="Request timed out.")
    internet_probe = FakeInternetProbe(ConnectivityResult(ok=False, code="NETWORK_ERROR", error="dns failed"))
    monitor = MonitorService(
        Settings(check_retry_delay_seconds=0, default_admin_qq="2087900785"),
        secret_box,
        notifier,
        probe=SequenceProbe([timeout, timeout]),
        internet_probe=internet_probe,
    )

    await monitor.check_config(session, config, scheduled=True, notify=True)

    assert session.scalar(select(CheckRecord)) is None
    assert len(notifier.messages) == 1
    target, message = notifier.messages[0]
    assert target.target_type == "private"
    assert target.target_id == "2087900785"
    assert "当前国际互联网连接断开" in message
    assert "不会计入可用性" in message
