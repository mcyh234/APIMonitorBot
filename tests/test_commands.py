from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.availability import CheckResult
from backend.app.api import list_sub2_prices
from backend.app.codex_radar import parse_codex_radar_payload
from backend.app.command_settings import set_command_aliases
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
from backend.app.model_pricing import ModelTokenPrice
from backend.app.sub2api import Sub2AuthTokens, Sub2AvailableCatalog, Sub2ChannelRateSnapshot
from backend.app.time_utils import utc_now
from backend.app.tibo_radar import TiboPost, TiboPresence, TiboRadarReport


class FakeProbe:
    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    async def probe(self, base_url: str, api_key: str, model_name: str):
        self.calls.append((base_url, api_key, model_name))
        return CheckResult(ok=True, code="200", latency_ms=12, response_preview="hi")


class FakeSnapshotter:
    def __init__(self):
        self.calls = 0

    async def capture(self) -> bytes:
        self.calls += 1
        return b"fake-png"


class FakeRadarClient:
    def __init__(self):
        self.calls = 0

    async def fetch(self):
        self.calls += 1
        point = {
            "date": "2026-07-13-pm",
            "score": 105,
            "passed": 7,
            "tasks": 10,
            "cost_usd": 18.94,
            "model": "gpt-5.6-luna",
            "reasoning_effort": "max",
        }
        return parse_codex_radar_payload(
            {
                "monitored_at": "2026-07-13T22:08:07+08:00",
                "timezone": "Asia/Shanghai",
                "model_iq": {"latest": point, "recent_days": [point], "comparisons": {}},
            },
            source_url="https://codexradar.com/current.json",
        )


class FakeTiboClient:
    def __init__(self):
        self.calls = 0

    async def fetch(self):
        self.calls += 1
        now = utc_now()
        return TiboRadarReport(
            monitored_at=now,
            timezone="Asia/Shanghai",
            presence=TiboPresence(
                location_zh="旧金山湾区 / PT",
                location_en="San Francisco Bay Area / PT",
                probability=0.3,
                confidence="low",
                evidence_zh="公开帖子与 PT 时区大致相符。",
                evidence_en="Public posts align with PT.",
                safety_note_zh="仅展示公开粗粒度信息。",
                observations=40,
                observed_at=now,
                updated_at=now,
            ),
            post=TiboPost(
                source_url="https://x.com/thsottiaux/status/123",
                author_name="Tibo",
                username="thsottiaux",
                text="Morning. Three updates.",
                translated_zh="早上好。三项更新。",
                translation_label="中文翻译 · 机器翻译",
                created_at=now,
                replies=1,
                reposts=2,
                likes=3,
                views=4,
                avatar=None,
            ),
        )


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

    async def fetch_available_catalog_with_cached_token(self, session, config, secret_box):
        self.fetch_calls += 1
        return Sub2AvailableCatalog(
            rates=tuple(self.rates),
            model_prices=(
                ModelTokenPrice(
                    model_name="gpt-5.6-sol",
                    platform="openai",
                    input_price=0.0000025,
                    output_price=0.000015,
                    cache_write_price=0,
                    cache_read_price=0.00000025,
                ),
            ),
        )


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
        ("G1112222333&amp;G1122334455", "请输入BaseURL"),
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
    assert config.target_type == "multi"
    assert config.target_id == "G1112222333&G1122334455"


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
async def test_handle_event_logs_alias_as_command():
    session = make_session()
    set_command_aliases(session, "/list", ["列表"])
    router = CommandRouter(Settings(), FakeOneBot(), SecretBox("test-key"), probe=FakeProbe())
    await router.handle_event(
        session,
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": 2087900785,
            "raw_message": "列表",
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
async def test_group_commands_match_multi_target_configs():
    session = make_session()
    secret_box = SecretBox("test-key")
    session.add(
        APIConfig(
            name="cfg-multi",
            target_type="multi",
            target_id="G123456&P2087900785",
            base_url="https://example.com/v1",
            api_key_encrypted=secret_box.encrypt("sk-test"),
            model_name="gpt-test",
            enabled=True,
        )
    )
    session.commit()
    onebot = FakeOneBot()
    router = CommandRouter(Settings(), onebot, secret_box, probe=FakeProbe())

    check_reply = await router.handle_message(
        session,
        IncomingMessage(user_id="10001", message="/check cfg-multi", message_type="group", group_id="123456"),
    )
    status_reply = await router.handle_message(
        session,
        IncomingMessage(user_id="10001", message="/status cfg-multi", message_type="group", group_id="123456"),
    )

    assert "当前服务可用" in (check_reply or "")
    assert status_reply == ""
    assert onebot.sent == [("group", "123456", "[image:status.png]")]

@pytest.mark.asyncio
async def test_check_without_args_checks_all_group_bound_configs():
    session = make_session()
    secret_box = SecretBox("test-key")
    session.add_all(
        [
            APIConfig(
                name="cfg-a",
                target_type="group",
                target_id="123456",
                base_url="https://example.com/a/v1",
                api_key_encrypted=secret_box.encrypt("sk-test"),
                model_name="gpt-test",
                enabled=True,
            ),
            APIConfig(
                name="cfg-b",
                target_type="multi",
                target_id="G123456&P2087900785",
                base_url="https://example.com/b/v1",
                api_key_encrypted=secret_box.encrypt("sk-test"),
                model_name="gpt-test",
                enabled=True,
            ),
            APIConfig(
                name="cfg-other",
                target_type="group",
                target_id="999999",
                base_url="https://example.com/other/v1",
                api_key_encrypted=secret_box.encrypt("sk-test"),
                model_name="gpt-test",
                enabled=True,
            ),
        ]
    )
    session.commit()
    onebot = FakeOneBot()
    probe = FakeProbe()
    router = CommandRouter(Settings(), onebot, secret_box, probe=probe)

    reply = await router.handle_message(
        session,
        IncomingMessage(user_id="2087900785", message="/check", message_type="group", group_id="123456"),
    )

    assert reply == ""
    assert onebot.sent == [("group", "123456", "[image:api-check.png]")]
    assert [call[0] for call in probe.calls] == [
        "https://example.com/a/v1",
        "https://example.com/b/v1",
    ]
    send_row = session.scalar(select(SendRecord).where(SendRecord.message_preview == "[image:api-check.png]"))
    assert send_row is not None


@pytest.mark.asyncio
async def test_check_without_args_uses_image_even_for_one_group_config():
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

    reply = await router.handle_message(
        session,
        IncomingMessage(user_id="10001", message="/check", message_type="group", group_id="123456"),
    )

    assert reply == ""
    assert onebot.sent == [("group", "123456", "[image:api-check.png]")]

    text_reply = await router.handle_message(
        session,
        IncomingMessage(user_id="2087900785", message="/check cfg-one", message_type="group", group_id="123456"),
    )

    assert text_reply is not None
    assert "\u3010cfg-one\u3011" in text_reply
    assert "\u5f53\u524d\u670d\u52a1\u53ef\u7528: 200" in text_reply


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
async def test_status_command_alias_sends_group_status_image():
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
    set_command_aliases(session, "/status", ["状态"])
    onebot = FakeOneBot()
    router = CommandRouter(Settings(), onebot, secret_box, probe=FakeProbe())

    reply = await router.handle_message(
        session,
        IncomingMessage(user_id="10001", message="状态", message_type="group", group_id="123456"),
    )

    assert reply == ""
    assert onebot.sent == [("group", "123456", "[image:status.png]")]

@pytest.mark.asyncio
async def test_status_command_records_image_failure_without_followup_reply():
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
    onebot = FakeOneBot(ok=False, error="ws timeout")
    router = CommandRouter(Settings(), onebot, secret_box, probe=FakeProbe())
    incoming = IncomingMessage(user_id="10001", message="/status", message_type="group", group_id="123456")

    reply = await router.handle_message(session, incoming)

    assert reply == ""
    assert onebot.sent == [("group", "123456", "[image:status.png]")]
    send_row = session.scalar(select(SendRecord).where(SendRecord.message_preview == "[image:status.png]"))
    assert send_row is not None
    assert send_row.ok is False
    assert send_row.error == "ws timeout"


@pytest.mark.asyncio
async def test_status_without_group_id_does_not_reply_permission_denied():
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
    incoming = IncomingMessage(user_id="10001", message="/status", message_type="group", group_id=None)

    reply = await router.handle_message(session, incoming)

    assert reply == ""
    assert onebot.sent == []


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
async def test_radar_command_sends_image_and_uses_cooldown():
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
    radar = FakeRadarClient()
    router = CommandRouter(Settings(), onebot, secret_box, probe=FakeProbe(), radar_client=radar)
    incoming = IncomingMessage(user_id="10001", message="/radar", message_type="group", group_id="123456")

    assert await router.handle_message(session, incoming) == ""
    assert onebot.sent == [("group", "123456", "[image:codex-radar.png]")]
    assert radar.calls == 1

    reply = await router.handle_message(session, incoming)
    assert "操作太频繁" in (reply or "")
    assert radar.calls == 1


@pytest.mark.asyncio
async def test_tibo_command_sends_image_and_uses_cooldown():
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
    tibo = FakeTiboClient()
    router = CommandRouter(Settings(), onebot, secret_box, probe=FakeProbe(), tibo_client=tibo)
    incoming = IncomingMessage(user_id="10001", message="/tibo", message_type="group", group_id="123456")

    assert await router.handle_message(session, incoming) == ""
    assert onebot.sent == [("group", "123456", "[image:tibo-radar.png]")]
    assert tibo.calls == 1

    reply = await router.handle_message(session, incoming)
    assert "操作太频繁" in (reply or "")
    assert tibo.calls == 1


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


@pytest.mark.asyncio
async def test_up_down_commands_vote_globally_and_bare_alias_works():
    session = make_session()
    secret_box = SecretBox("test-key")
    add_sub2_config(session, secret_box)
    router = CommandRouter(Settings(), FakeOneBot(), secret_box, probe=FakeProbe())

    first = await router.handle_message(
        session,
        IncomingMessage(user_id="10001", message="up", message_type="group", group_id="123456"),
    )
    changed = await router.handle_message(
        session,
        IncomingMessage(user_id="10001", message="/down", message_type="group", group_id="123456"),
    )
    second_user = await router.handle_message(
        session,
        IncomingMessage(user_id="10002", message="/up", message_type="group", group_id="123456"),
    )

    assert "已记录今日看涨" in (first or "")
    assert "改为看跌" in (changed or "")
    assert "看涨 50.0% · 看跌 50.0% · 共 2 票" in (second_user or "")


@pytest.mark.asyncio
async def test_sentiment_vote_requires_bound_group_but_allows_admin_private():
    session = make_session()
    secret_box = SecretBox("test-key")
    add_sub2_config(session, secret_box)
    router = CommandRouter(Settings(), FakeOneBot(), secret_box, probe=FakeProbe())

    denied = await router.handle_message(
        session,
        IncomingMessage(user_id="10001", message="up", message_type="group", group_id="999999"),
    )
    private_denied = await router.handle_message(
        session,
        IncomingMessage(user_id="10001", message="down", message_type="private"),
    )
    admin = await router.handle_message(
        session,
        IncomingMessage(user_id="2087900785", message="down", message_type="private"),
    )

    assert "不是 Sub2API 通知对象" in (denied or "")
    assert "私聊投票仅限管理员" in (private_denied or "")
    assert "已记录今日看跌" in (admin or "")


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
