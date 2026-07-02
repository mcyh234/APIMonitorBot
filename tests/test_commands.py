from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.availability import CheckResult
from backend.app.api import list_sub2_prices
from backend.app.commands import CommandRouter, IncomingMessage
from backend.app.crypto import SecretBox
from backend.app.models import (
    APIConfig,
    Base,
    BotAdmin,
    ReceivedMessage,
    SendRecord,
    Sub2ChannelRate,
    Sub2Config,
    Sub2RateHistory,
)
from backend.app.onebot import OneBotClient, OneBotSendResult
from backend.app.settings import Settings
from backend.app.sub2api import Sub2AuthTokens, Sub2ChannelRateSnapshot
from backend.app.time_utils import utc_now


class FakeProbe:
    async def probe(self, base_url: str, api_key: str, model_name: str):
        return CheckResult(ok=True, code="200", latency_ms=12, response_preview="hi")


class FakeSnapshotter:
    def __init__(self):
        self.calls = 0

    async def capture(self) -> bytes:
        self.calls += 1
        return b"fake-png"


class FakeSub2Client:
    def __init__(self, rates: list[Sub2ChannelRateSnapshot] | None = None):
        self.rates = rates or [
            Sub2ChannelRateSnapshot("openai", "2", "OpenAi", 0.06),
            Sub2ChannelRateSnapshot("anthropic", "19", "ClaudeCode Max20", 0.85),
        ]
        self.login_calls = 0
        self.fetch_calls = 0

    async def login(self, base_url: str, email: str, password: str):
        self.login_calls += 1
        return Sub2AuthTokens("access-token", "refresh-token", utc_now() + timedelta(hours=1))

    async def fetch_rates(self, base_url: str, access_token: str):
        self.fetch_calls += 1
        return list(self.rates)

    async def fetch_rates_with_cached_token(self, session, config, secret_box):
        self.fetch_calls += 1
        return list(self.rates)


class FakeOneBot(OneBotClient):
    def __init__(self, ok: bool = True, error: str | None = None, status_code: int | None = None):
        super().__init__(Settings())
        self.ok = ok
        self.error = error
        self.status_code = status_code
        self.sent: list[tuple[str, str, str]] = []

    async def send_group_msg(self, group_id: str, message: str) -> OneBotSendResult:
        self.sent.append(("group", group_id, message))
        return OneBotSendResult(
            ok=self.ok,
            payload={"status": "ok"} if self.ok else {"status": "failed", "message": self.error},
            error=self.error,
            action="send_group_msg",
            target_type="group",
            target_id=str(group_id),
            message=message,
            status_code=self.status_code,
        )

    async def send_private_msg(self, user_id: str, message: str) -> OneBotSendResult:
        self.sent.append(("private", user_id, message))
        return OneBotSendResult(
            ok=self.ok,
            payload={"status": "ok"} if self.ok else {"status": "failed", "message": self.error},
            error=self.error,
            action="send_private_msg",
            target_type="private",
            target_id=str(user_id),
            message=message,
            status_code=self.status_code,
        )

    async def send_image_message(
        self,
        target_type: str,
        target_id: str,
        image_bytes: bytes,
        filename: str = "status.png",
    ) -> OneBotSendResult:
        self.sent.append((target_type, target_id, f"[image:{filename}]"))
        return OneBotSendResult(
            ok=self.ok,
            payload={"status": "ok"} if self.ok else {"status": "failed", "message": self.error},
            error=self.error,
            action="send_group_msg" if target_type == "group" else "send_private_msg",
            target_type=target_type,
            target_id=str(target_id),
            message=f"[image:{filename}]",
            status_code=self.status_code,
        )

    async def is_in_group(self, group_id: str):
        return True


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    session.add(BotAdmin(qq="2087900785"))
    session.commit()
    return session


def add_sub2_config(session, secret_box, name="sub2-one", target_type="group", target_id="123456"):
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
    session.add_all(
        [
            Sub2ChannelRate(
                sub2_config_id=config.id,
                platform="openai",
                group_key="2",
                group_name="OpenAi",
                rate_multiplier=0.06,
            ),
            Sub2ChannelRate(
                sub2_config_id=config.id,
                platform="anthropic",
                group_key="19",
                group_name="ClaudeCode Max20",
                rate_multiplier=0.85,
            ),
        ]
    )
    session.commit()
    return config


@pytest.mark.asyncio
async def test_addapi_conversation_creates_config():
    session = make_session()
    router = CommandRouter(Settings(), FakeOneBot(), SecretBox("test-key"), probe=FakeProbe())
    incoming = IncomingMessage(user_id="2087900785", message="/addapi", message_type="private")
    assert await router.handle_message(session, incoming) == "请输入api配置名称"

    steps = [
        ("cfg-one", "请输入报告群号/私聊QQ号"),
        ("G123456", "请输入BaseURL"),
        ("https://example.com/v1", "请输入APIKey"),
        ("sk-test", "请输入监听模型名称"),
        ("gpt-test", "添加成功"),
    ]
    for text, expected in steps:
        incoming.message = text
        reply = await router.handle_message(session, incoming)
        assert expected in (reply or "")

    config = session.scalar(select(APIConfig).where(APIConfig.name == "cfg-one"))
    assert config is not None
    assert config.target_type == "group"
    assert config.target_id == "123456"


@pytest.mark.asyncio
async def test_addsub2_conversation_creates_config_and_initial_rates():
    session = make_session()
    secret_box = SecretBox("test-key")
    sub2_client = FakeSub2Client()
    router = CommandRouter(
        Settings(),
        FakeOneBot(),
        secret_box,
        probe=FakeProbe(),
        sub2_client=sub2_client,
    )
    incoming = IncomingMessage(user_id="2087900785", message="/addsub2", message_type="private")
    assert await router.handle_message(session, incoming) == "请输入API名称"

    steps = [
        ("gptstore", "请输入Sub2API的BaseURL"),
        ("https://pool.example.com/", "请输入email"),
        ("bot@example.com", "请输入密码"),
        ("A123456", "登录成功"),
        ("G123456", "添加成功"),
    ]
    for text, expected in steps:
        incoming.message = text
        reply = await router.handle_message(session, incoming)
        assert expected in (reply or "")

    config = session.scalar(select(Sub2Config).where(Sub2Config.name == "gptstore"))
    assert config is not None
    assert config.target_type == "group"
    assert config.target_id == "123456"
    rates = session.scalars(select(Sub2ChannelRate).where(Sub2ChannelRate.sub2_config_id == config.id)).all()
    assert len(rates) == 2
    history = session.scalars(select(Sub2RateHistory).where(Sub2RateHistory.sub2_config_id == config.id)).all()
    assert len(history) == 2
    assert sub2_client.login_calls == 1


@pytest.mark.asyncio
async def test_handle_event_logs_triggered_message():
    session = make_session()
    router = CommandRouter(Settings(), FakeOneBot(), SecretBox("test-key"), probe=FakeProbe())
    await router.handle_event(
        session,
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": 2087900785,
            "raw_message": "/list",
        },
    )
    row = session.scalar(select(ReceivedMessage).where(ReceivedMessage.user_id == "2087900785"))
    assert row is not None
    assert row.triggered is True
    assert row.trigger_type == "command:/list"


@pytest.mark.asyncio
async def test_handle_event_records_send_failure_reason():
    session = make_session()
    onebot = FakeOneBot(ok=False, error="HTTP 403", status_code=403)
    router = CommandRouter(Settings(), onebot, SecretBox("test-key"), probe=FakeProbe())
    await router.handle_event(
        session,
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": 2087900785,
            "raw_message": "/list",
        },
    )

    row = session.scalar(select(SendRecord).where(SendRecord.ok.is_(False)))
    assert row is not None
    assert row.action == "send_private_msg"
    assert row.target_type == "private"
    assert row.target_id == "2087900785"
    assert row.status_code == 403
    assert row.error == "HTTP 403"
    assert "当前没有正在运行的 API 配置" in row.message_preview


@pytest.mark.asyncio
async def test_status_command_sends_group_status_image_and_uses_cooldown():
    session = make_session()
    secret_box = SecretBox("test-key")
    session.add(
        APIConfig(
            name="cfg-one",
            target_type="group",
            target_id="123456",
            base_url="https://example.com/v1",
            api_key_encrypted=secret_box.encrypt("sk-test"),
            model_name="gpt-test",
            enabled=True,
        )
    )
    session.commit()
    onebot = FakeOneBot()
    router = CommandRouter(Settings(), onebot, secret_box, probe=FakeProbe())
    incoming = IncomingMessage(user_id="10001", message="/status", message_type="group", group_id="123456")

    reply = await router.handle_message(session, incoming)

    assert reply == ""
    assert onebot.sent == [("group", "123456", "[image:status.png]")]
    send_row = session.scalar(select(SendRecord).where(SendRecord.message_preview == "[image:status.png]"))
    assert send_row is not None

    reply = await router.handle_message(session, incoming)

    assert "操作太频繁" in (reply or "")
    assert onebot.sent == [("group", "123456", "[image:status.png]")]


@pytest.mark.asyncio
async def test_stat_command_sends_web_snapshot_to_notification_group_and_uses_cooldown():
    session = make_session()
    secret_box = SecretBox("test-key")
    session.add(
        APIConfig(
            name="cfg-one",
            target_type="group",
            target_id="123456",
            base_url="https://example.com/v1",
            api_key_encrypted=secret_box.encrypt("sk-test"),
            model_name="gpt-test",
            enabled=True,
        )
    )
    session.commit()
    onebot = FakeOneBot()
    snapshotter = FakeSnapshotter()
    router = CommandRouter(Settings(), onebot, secret_box, probe=FakeProbe(), snapshotter=snapshotter)
    incoming = IncomingMessage(user_id="10001", message="/stat", message_type="group", group_id="123456")

    reply = await router.handle_message(session, incoming)

    assert reply == ""
    assert snapshotter.calls == 1
    assert onebot.sent == [("group", "123456", "[image:gptstore-status.png]")]
    send_row = session.scalar(select(SendRecord).where(SendRecord.message_preview == "[image:gptstore-status.png]"))
    assert send_row is not None

    reply = await router.handle_message(session, incoming)

    assert "操作太频繁" in (reply or "")
    assert snapshotter.calls == 1


@pytest.mark.asyncio
async def test_price_command_sends_sub2_price_image_and_uses_cooldown():
    session = make_session()
    secret_box = SecretBox("test-key")
    add_sub2_config(session, secret_box)
    onebot = FakeOneBot()
    router = CommandRouter(Settings(), onebot, secret_box, probe=FakeProbe(), sub2_client=FakeSub2Client())
    incoming = IncomingMessage(user_id="10001", message="/price", message_type="group", group_id="123456")

    reply = await router.handle_message(session, incoming)

    assert reply == ""
    assert onebot.sent == [("group", "123456", "[image:sub2-price.png]")]

    reply = await router.handle_message(session, incoming)

    assert "操作太频繁" in (reply or "")
    assert onebot.sent == [("group", "123456", "[image:sub2-price.png]")]


def test_sub2_prices_api_returns_current_rates_and_history():
    session = make_session()
    secret_box = SecretBox("test-key")
    add_sub2_config(session, secret_box)

    boards = list_sub2_prices(session)

    assert len(boards) == 1
    assert boards[0].name == "sub2-one"
    assert boards[0].target == "G123456"
    assert len(boards[0].rates) == 2
    assert boards[0].rates[0].history


@pytest.mark.asyncio
async def test_admin_bypasses_command_cooldown():
    session = make_session()
    secret_box = SecretBox("test-key")
    onebot = FakeOneBot()
    snapshotter = FakeSnapshotter()
    router = CommandRouter(Settings(), onebot, secret_box, probe=FakeProbe(), snapshotter=snapshotter)
    incoming = IncomingMessage(user_id="2087900785", message="/stat", message_type="private")

    first = await router.handle_message(session, incoming)
    second = await router.handle_message(session, incoming)

    assert first == ""
    assert second == ""
    assert snapshotter.calls == 2
    assert onebot.sent == [
        ("private", "2087900785", "[image:gptstore-status.png]"),
        ("private", "2087900785", "[image:gptstore-status.png]"),
    ]
