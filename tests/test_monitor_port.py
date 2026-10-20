import asyncio
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from backend.app import db
from backend.app.api import status_bars_to_out
from backend.app.app_settings import set_app_setting
from backend.app.availability import ApiProbe, CheckResult
from backend.app.crypto import SecretBox
from backend.app.intelligence import IntelPipeline, IntelSettings, LLMSettings, finding_detail, findings, llm_transcript, llm_view, put_llm, safe_text
from backend.app.material_theme import new_theme
from backend.app.models import Base, BotAdmin, CheckRecord, ConversationState, IntelFinding, IntelMessage, ProbeObservation, PublicPriceSnapshot
from backend.app.monitor import MonitorService
from backend.app.monitor_extensions import AdvancedSettings, PublicRule, PublicSettings, apply_advanced_settings, public_status, update_public_settings
from backend.app.repository import get_conversation, today_availability, upsert_conversation
from backend.app.settings import Settings
from backend.app.status_bars import StatusBucketData, StatusWindowData, build_status_bars, should_render_timeout
from backend.app.status_image import render_status_image
from backend.app.time_utils import utc_now
from test_monitor import FakeNotifier, SequenceProbe, add_config, add_sub2_config


@pytest.fixture
def database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session, factory
    engine.dispose()


@pytest.fixture
def approved_delivery_unit(monkeypatch):
    """Delivery state-machine tests isolate approval; policy has real-path tests below."""
    monkeypatch.setattr("backend.app.tibo_watch.moderator_config", lambda session: {})
    async def approved(*args):
        return None
    monkeypatch.setattr("backend.app.tibo_watch.review_report", approved)


@pytest.mark.asyncio
@pytest.mark.parametrize("base,protocol", [("https://api.test", "auto"), ("https://api.test/v1/messages", "anthropic")])
async def test_claude_messages(base, protocol):
    async def handle(request):
        assert str(request.url) == "https://api.test/v1/messages"
        assert request.headers["x-api-key"] == "secret"
        assert "authorization" not in request.headers
        assert json.loads(request.content)["max_tokens"] == 1
        return httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await ApiProbe(client=client).probe(base, "secret", "claude-test", protocol=protocol)
        assert result.ok


@pytest.mark.asyncio
async def test_claude_inferred_fallback_and_explicit_strict():
    calls = []
    async def handle(request):
        calls.append(request.url.path)
        return httpx.Response(404) if request.url.path.endswith("messages") else httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        probe = ApiProbe(client=client)
        assert (await probe.probe("https://api.test/v1", "key", "claude")).ok
        assert calls == ["/v1/messages", "/v1/chat/completions"]
        calls.clear()
        assert not (await probe.probe("https://api.test/v1", "key", "claude", protocol="anthropic")).ok
        assert calls == ["/v1/messages"]
        assert (await probe.probe("https://api.test", "key", "claude", protocol="invalid")).code == "INVALID_PROTOCOL"


@pytest.mark.asyncio
async def test_probe_never_follows_redirect():
    calls = []
    async def handle(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://other.test"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        assert (await ApiProbe(client=client).probe("https://api.test", "secret", "gpt")).code == "302"
        assert len(calls) == 1


def test_legacy_sqlite_migration_preserves_tls(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE api_configs (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT INTO api_configs VALUES (1, 'legacy')"))
        conn.execute(text("CREATE TABLE check_records (id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr(db, "engine", engine)
    db._ensure_schema_compatibility()
    db._ensure_schema_compatibility()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT protocol, verify_tls FROM api_configs")).one()
        assert row == ("auto", 0)
    assert "model_switched" in {c["name"] for c in inspect(engine).get_columns("check_records")}


@pytest.mark.asyncio
async def test_timeout_observation_separate_from_availability(database):
    session, _ = database
    box = SecretBox("test")
    config = add_config(session, box)
    service = MonitorService(Settings(check_retry_delay_seconds=0), box, FakeNotifier(),
        probe=SequenceProbe([CheckResult(False, "TIMEOUT", latency_ms=20000), CheckResult(True, "200", latency_ms=450)]))
    await service.check_config(session, config, True, False)
    assert len(list(session.scalars(select(ProbeObservation)))) == 1
    assert len(list(session.scalars(select(CheckRecord)))) == 1
    assert today_availability(session, config.id, "Asia/Shanghai") == 100
    bars = build_status_bars(session, [config], "Asia/Shanghai")
    response = status_bars_to_out(bars[0])
    assert sum(b.timeout_count for b in response.windows[0].buckets) == 1
    assert len(response.windows[0].latency_points) == 2


@pytest.mark.asyncio
async def test_config_lock_and_release(database):
    session, _ = database
    box = SecretBox("test")
    config = add_config(session, box)
    started, release = asyncio.Event(), asyncio.Event()
    class Probe:
        async def probe(self, *args):
            started.set()
            await release.wait()
            return CheckResult(True, "200")
    service = MonitorService(Settings(), box, FakeNotifier(), probe=Probe())
    task = asyncio.create_task(service.check_config(session, config, False, False))
    await started.wait()
    assert (await service.check_config(session, config, True, False)).code == "CHECK_IN_PROGRESS"
    release.set()
    await task
    assert (await service.check_config(session, config, False, False)).ok


@pytest.mark.parametrize("minutes,timeouts,successes,expected", [(1, 1, 20, True), (10, 2, 3, False), (10, 3, 4, True), (60, 0, 0, False)])
def test_timeout_threshold(minutes, timeouts, successes, expected):
    now = datetime.now(timezone.utc)
    bucket = StatusBucketData(now, now, "ok", successes, 0, successes, timeouts > 0, timeouts)
    assert should_render_timeout(StatusWindowData("test", "test", minutes, [bucket])) == expected


def test_rolling_success_rate_across_midnight(database, monkeypatch):
    session, _ = database
    from backend.app import repository
    now = datetime(2026, 9, 25, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(repository, "utc_now", lambda: now)
    config = add_config(session, SecretBox("test"))
    for age, status in [(25, "down"), (20, "down"), (1, "ok")]:
        session.add(CheckRecord(api_config_id=config.id, checked_at=now - timedelta(hours=age), status=status, scheduled=True))
    session.commit()
    assert today_availability(session, config.id, "Asia/Shanghai") == 50


def test_conversation_encrypted_roundtrip(database):
    session, _ = database
    session.info["conversation_secret_box"] = SecretBox("test")
    upsert_conversation(session, "123", "sub2_target", {"password": "secret-password"})
    stored = session.scalar(select(ConversationState))
    assert "secret-password" not in json.dumps(stored.payload)
    assert get_conversation(session, "123").payload["password"] == "secret-password"


@pytest.mark.parametrize("hour,dark", [(6, True), (7, False), (18, False), (19, True), (23, True)])
def test_material_beijing_boundaries(hour, dark):
    from zoneinfo import ZoneInfo
    at = datetime(2026, 9, 25, hour, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert new_theme(at, hue=140).dark is dark
    assert new_theme(at.astimezone(timezone.utc), hue=140).dark is dark


def test_material_each_render_changes_theme():
    themes = [new_theme() for _ in range(30)]
    assert all(a.hue != b.hue and a.primary != b.primary for a, b in zip(themes, themes[1:]))


def test_status_day_night_pixels_and_size(database):
    session, _ = database
    config = add_config(session, SecretBox("test"))
    bars = build_status_bars(session, [config], "Asia/Shanghai")
    day = render_status_image(bars, timezone_name="Asia/Shanghai", generated_at=datetime(2026, 9, 25, 4, tzinfo=timezone.utc))
    night = render_status_image(bars, timezone_name="Asia/Shanghai", generated_at=datetime(2026, 9, 25, 14, tzinfo=timezone.utc))
    for png, is_dark in [(day, False), (night, True)]:
        image = Image.open(BytesIO(png))
        assert image.size == (1120, 422)
        assert (sum(image.getpixel((0, 100))) < 200) is is_dark
    assert day != night


def test_advanced_persistence(database):
    session, _ = database
    values = AdvancedSettings(checker_enabled=False, request_timeout_seconds=45, check_interval_seconds=30)
    set_app_setting(session, "monitor.advanced", values.model_dump_json())
    settings = Settings()
    apply_advanced_settings(session, settings)
    assert settings.checker_enabled is False and settings.request_timeout_seconds == 45


def test_public_whitelist_disabled_and_no_private_fields(database):
    session, _ = database
    config = add_sub2_config(session, SecretBox("test"))
    with pytest.raises(HTTPException) as exc:
        public_status(session)
    assert exc.value.status_code == 404
    update_public_settings(PublicSettings(enabled=True), session)
    assert public_status(session)["sections"] == []
    update_public_settings(PublicSettings(enabled=True, rules=[PublicRule(config_id=config.id, platform="openai", group_key="2", visible=True)]), session)
    result = public_status(session)
    assert result["sections"][0]["rows"][0]["ratio"] == .1
    output = json.dumps(result, default=str)
    assert "pool.example" not in output and "bot@example.com" not in output and "target" not in output
    config.enabled = False
    session.commit()
    assert public_status(session)["sections"][0]["rows"][0]["status"] == "paused"


def test_llm_key_encrypted_and_never_in_view(database):
    session, _ = database
    box = SecretBox("test")
    put_llm(LLMSettings(base_url="https://api.test/v1", model="gpt", api_key="secret-key"), session, box)
    assert "secret-key" not in json.dumps(llm_view(session))
    assert llm_view(session)["api_key_configured"]
    put_llm(LLMSettings(base_url="https://api.test/v1", model="gpt"), session, box)
    assert llm_view(session)["api_key_configured"]
    put_llm(LLMSettings(clear_api_key=True), session, box)
    assert not llm_view(session)["api_key_configured"]


@pytest.mark.asyncio
async def test_intel_allowlist_encryption_dedupe_and_notification(database):
    session, factory = database
    box, notifier = SecretBox("test"), FakeNotifier()
    session.add(BotAdmin(qq="11111"))
    session.commit()
    set_app_setting(session, "intel.settings", IntelSettings(enabled=True, group_ids=["123"], settle_seconds=0).model_dump_json())
    pipeline = IntelPipeline(factory, box, notifier)
    message = SimpleNamespace(message_type="private", group_id="123", user_id="222", message="福利 注册 倍率 0.01x https://api.test/join?token=secret")
    pipeline.observe({"message_id": 1}, message)
    assert session.scalar(select(IntelMessage)) is None
    message.message_type = "group"
    message.group_id = "999"
    pipeline.observe({"message_id": 1}, message)
    assert session.scalar(select(IntelMessage)) is None
    message.group_id = "123"
    pipeline.observe({"message_id": 1}, message)
    await asyncio.gather(*list(pipeline.tasks))
    row = session.scalar(select(IntelMessage))
    assert "福利" not in row.payload_cipher and "secret" not in box.decrypt(row.payload_cipher)
    records = findings(session)
    assert records[0]["status"] == "reported"
    assert "context" not in records[0]
    detail = finding_detail(records[0]["id"], session, box)
    assert detail["urls"] == ["https://api.test/join"]
    assert len(notifier.messages) == 1
    pipeline.observe({"message_id": 2}, message)
    await asyncio.gather(*list(pipeline.tasks))
    assert len(findings(session)) == 1
    await pipeline.close()


def test_intel_sanitizes_credentials():
    value = safe_text("password=secret https://user:pass@example.com/join?api_key=hidden sk-123456789")
    assert "secret" not in value and "hidden" not in value and "user:pass" not in value and "sk-" not in value
    assert "quoted secret" not in safe_text('{"password": "quoted secret"}')
    assert safe_text("https://api.test/join?aff=123&access_token=secret") == "https://api.test/join?aff=123"


def test_llm_transcript_keeps_trigger_and_valid_bounded_json():
    context = [{"message": "很长的中文消息" * 600, "trigger": i == 24} for i in range(50)]
    transcript = llm_transcript(context)
    assert len(transcript.encode()) <= 32768
    assert any(row["trigger"] for row in json.loads(transcript))


def test_latency_outliers_match_source_algorithm():
    from backend.app.status_image import latency_outlier_indexes
    points = [{"latency_ms": value} for value in [100, 110, 90, 100, 10000]]
    assert latency_outlier_indexes(points) == {4}
    assert latency_outlier_indexes(points[:4]) == set()


def test_delete_config_removes_observations(database):
    from backend.app.repository import delete_api_config
    session, _ = database
    config = add_config(session, SecretBox("test"))
    session.add(ProbeObservation(api_config_id=config.id))
    session.commit()
    assert delete_api_config(session, config.name) == 1
    assert session.scalar(select(ProbeObservation)) is None


@pytest.mark.asyncio
async def test_sensitive_notification_does_not_leave_plaintext_audit(database):
    from backend.app.notifier import OneBotNotifier, NotifyTarget
    from backend.app.onebot import OneBotSendResult
    from backend.app.models import SendRecord
    session, factory = database
    class Client:
        async def send_message(self, *args):
            return OneBotSendResult(False, payload={"echo": "private-content"}, error="private-content", message="private-content")
    await OneBotNotifier(Client(), factory).send_sensitive(NotifyTarget("private", "123"), "private-content")
    row = session.scalar(select(SendRecord))
    assert "private-content" not in str((row.message_preview, row.error, row.response_payload))


def newapi_pricing_payload():
    def model(name, **values):
        return {"model_name": name, "quota_type": 0, "model_ratio": 1.25,
                "completion_ratio": 4, "enable_groups": ["standard"], **values}
    return {
        "success": True,
        "group_ratio": {"standard": 0.2, "free": 0, "staff": 1, "auto": "自动", "broken": -1},
        "usable_group": {"standard": "标准分组说明", "free": "免费分组", "auto": "自动"},
        "data": [
            model("token-model", cache_ratio=0.1, create_cache_ratio=1.25),
            model("request-model", quota_type=1, model_price=0.03),
            model("linear-model", billing_mode="tiered_expr", model_ratio=999,
                  billing_expr='tier("base", p * 3 + c * 15 + cr * 0.3 + cc * 3.75 + cc1h * 6)'),
            model("dynamic-model", billing_mode="tiered_expr", model_ratio=999,
                  billing_expr='len > 10000 ? tier("long", p * 6 + c * 30) : tier("base", p * 3 + c * 15)'),
            model("zero-model", model_ratio=0, enable_groups=["all"]),
            model("hidden-model", enable_groups=["staff"]),
        ],
    }


def test_newapi_prices_use_plaza_units_currency_and_model_group_membership():
    from backend.app.newapi_pricing import flatten_newapi_catalog
    from backend.app.model_pricing import model_prices_for_group, token_price_to_cny_per_mtok
    catalog = flatten_newapi_catalog(newapi_pricing_payload(), {"quota_display_type": "CNY", "usd_exchange_rate": 7})
    assert [(r.group_key, r.rate_multiplier) for r in catalog.rates] == [("free", 0), ("standard", 0.2)]
    models = {p.model_name: p for p in catalog.model_prices}
    assert "hidden-model" not in models
    price = token_price_to_cny_per_mtok(models["token-model"], 0.2)
    assert price.input == pytest.approx(3.5)
    assert price.output == pytest.approx(14)
    assert price.cache_read == pytest.approx(0.35)
    assert price.cache_write == pytest.approx(4.375)
    assert models["token-model"].currency == "CNY"
    assert models["request-model"].request_price * 0.2 == pytest.approx(0.042)
    assert models["request-model"].billing_mode == "request"
    assert models["dynamic-model"].billing_mode == "dynamic"
    linear = token_price_to_cny_per_mtok(models["linear-model"], 0.2)
    assert linear.input == pytest.approx(4.2)
    assert linear.output == pytest.approx(21)
    assert models["linear-model"].cache_write_1h_price * 1_000_000 * 0.2 == pytest.approx(8.4)
    assert models["zero-model"].cache_read_price is None
    assert token_price_to_cny_per_mtok(models["zero-model"], 0).input == 0
    free = model_prices_for_group(catalog.model_prices, platform="newapi", group_name="免费", group_key="free", limit=None)
    assert [p.model_name for p in free] == ["zero-model"]
    assert len(model_prices_for_group(catalog.model_prices, platform="newapi", group_name="standard", group_key="standard", limit=None)) == 5


@pytest.mark.parametrize("status,currency,rate", [
    ({}, "USD", 1), ({"quota_display_type": "USD", "usd_exchange_rate": 7}, "USD", 1),
    ({"quota_display_type": "CNY"}, "USD", 1),
    ({"quota_display_type": "CNY", "usd_exchange_rate": 0}, "USD", 1),
    ({"quota_display_type": "CUSTOM", "custom_currency_symbol": "积分", "custom_currency_exchange_rate": 3}, "积分", 3),
])
def test_newapi_display_currency_never_guesses_exchange_rate(status, currency, rate):
    from backend.app.newapi_pricing import flatten_newapi_catalog
    model = next(p for p in flatten_newapi_catalog(newapi_pricing_payload(), status).model_prices if p.model_name == "token-model")
    assert model.currency == currency
    assert model.input_price * 1_000_000 == pytest.approx(2.5 * rate)


@pytest.mark.parametrize("expression", [
    'tier("base", max(p * 3, 100) + c * 15)',
    'tier("base", p * 3 + c * 15) * 2',
    'tier("base", p * 3 + c * 15 + img * 5)',
    'tier("base", p * 3 + p * 2 + c * 15)',
    '__import__("os").system("echo forbidden")',
])
def test_newapi_unrecognized_expressions_remain_dynamic(expression):
    from backend.app.newapi_pricing import flatten_newapi_catalog
    data = newapi_pricing_payload()
    data["data"] = [{"model_name": "custom-model", "enable_groups": ["standard"],
                     "billing_mode": "tiered_expr", "billing_expr": expression, "model_ratio": 999}]
    model = flatten_newapi_catalog(data).model_prices[0]
    assert model.billing_mode == "dynamic"
    assert model.price_note


@pytest.mark.asyncio
async def test_newapi_catalog_reuses_credentials_and_normalizes_pricing_url(database):
    from backend.app.sub2api import Sub2ApiClient
    from backend.app.models import Sub2Config
    session, _ = database
    box = SecretBox("test")
    add_sub2_config(session, box)
    config = session.scalar(select(Sub2Config))
    config.upstream_type = "newapi"
    config.newapi_user_id = "42"
    config.base_url = "https://plaza.example/pricing?view=all#models"
    requests = []
    def handle(request):
        requests.append(request)
        if request.url.path == "/api/pricing":
            assert request.headers["New-Api-User"] == "42"
            assert request.headers["Authorization"] == "access-token"
            assert not request.url.query and not request.url.fragment
            return httpx.Response(200, json=newapi_pricing_payload())
        assert request.url.path == "/api/status"
        return httpx.Response(200, json={"success": True, "data": {"quota_display_type": "CNY", "usd_exchange_rate": 1}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        catalog = await Sub2ApiClient(client=client).fetch_available_catalog_with_cached_token(session, config, box)
    assert len(requests) == 2
    assert {p.currency for p in catalog.model_prices} == {"CNY"}


@pytest.mark.asyncio
async def test_newapi_expired_session_relogs_and_status_failure_keeps_usd_prices(database):
    from backend.app.sub2api import Sub2ApiClient
    from backend.app.models import Sub2Config
    session, _ = database
    box = SecretBox("test")
    add_sub2_config(session, box)
    config = session.scalar(select(Sub2Config))
    config.upstream_type, config.newapi_user_id = "newapi", "42"
    config.access_token_encrypted = box.encrypt("expired-token")
    config.session_cookie_encrypted = box.encrypt("session=expired")
    calls = []
    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/api/pricing":
            if request.headers.get("cookie") != "session=fresh":
                return httpx.Response(401, text="Session expired")
            assert "authorization" not in request.headers
            return httpx.Response(200, json=newapi_pricing_payload())
        if request.url.path == "/api/user/login":
            assert json.loads(request.content) == {"username": "bot@example.com", "password": "password"}
            return httpx.Response(200, json={"success": True, "data": {"id": 42}}, headers={"set-cookie": "session=fresh; Path=/; HttpOnly"})
        assert request.url.path == "/api/status"
        return httpx.Response(502, text="unavailable")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        catalog = await Sub2ApiClient(client=client).fetch_available_catalog_with_cached_token(session, config, box)
    assert calls == ["/api/pricing", "/api/user/login", "/api/pricing", "/api/status"]
    assert box.decrypt(config.session_cookie_encrypted) == "session=fresh"
    assert config.access_token_encrypted is None
    assert {p.currency for p in catalog.model_prices} == {"USD"}


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"success": False, "message": "denied"}, {"success": True, "data": []}, []])
async def test_newapi_catalog_rejects_error_or_incomplete_payload(payload):
    from backend.app.sub2api import Sub2ApiClient, Sub2ApiError
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as client:
        with pytest.raises(Sub2ApiError):
            await Sub2ApiClient(client=client).fetch_newapi_catalog("https://plaza.example")


@pytest.mark.asyncio
async def test_newapi_price_command_obeys_targets_cooldown_and_pause(database, monkeypatch):
    from backend.app.commands import CommandRouter, IncomingMessage
    from backend.app.sub2api import Sub2ApiClient
    from backend.app.models import Sub2Config
    from tests.test_commands import FakeOneBot, FakeProbe
    session, _ = database
    box = SecretBox("test")
    add_sub2_config(session, box, target_type="multi", target_id="G123&P456")
    config = session.scalar(select(Sub2Config))
    config.upstream_type, config.newapi_user_id = "newapi", "42"
    onebot, boards, requests = FakeOneBot(), [], []
    def render(items, **kwargs):
        boards.extend(items)
        return b"fake-png"
    monkeypatch.setattr("backend.app.commands.render_sub2_price_image", render)
    def handle(request):
        requests.append(request.url.path)
        return httpx.Response(200, json=newapi_pricing_payload() if request.url.path == "/api/pricing" else {"success": True, "data": {}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        router = CommandRouter(Settings(), onebot, box, probe=FakeProbe(), sub2_client=Sub2ApiClient(client=client))
        wrong_group = IncomingMessage("111", "/price", "group", "999")
        assert "权限不足" in await router.handle_message(session, wrong_group)
        assert requests == []
        incoming = IncomingMessage("111", "/price", "group", "123")
        assert await router.handle_message(session, incoming) == ""
        assert len(boards[0].model_prices) == 5
        assert {r.platform for r in boards[0].rates} == {"newapi"}
        assert len(requests) == 2
        assert "操作太频繁" in await router.handle_message(session, incoming)
        assert len(requests) == 2
        # Admin private requests still require a bound notification target.
        session.add_all([BotAdmin(qq="456"), BotAdmin(qq="789")])
        session.commit()
        assert "没有可显示" in await router.handle_message(session, IncomingMessage("789", "/price", "private"))
        config.enabled = False
        session.commit()
        assert await router.handle_message(session, IncomingMessage("456", "/price", "private")) == ""
        assert boards[-1].price_note and not boards[-1].model_prices
        assert len(requests) == 2
    assert onebot.sent == [("group", "123", "[image:sub2-price.png]"), ("private", "456", "[image:sub2-price.png]")]


@pytest.mark.asyncio
async def test_newapi_price_error_preserves_saved_rates_and_explains_missing_prices(database, monkeypatch):
    from backend.app.commands import CommandRouter, IncomingMessage
    from backend.app.models import Sub2Config
    from backend.app.sub2api import Sub2ApiError
    from tests.test_commands import FakeOneBot, FakeProbe
    session, _ = database
    box = SecretBox("test")
    add_sub2_config(session, box)
    config = session.scalar(select(Sub2Config))
    config.upstream_type = "newapi"
    boards = []
    class BrokenClient:
        async def fetch_available_catalog_with_cached_token(self, *args):
            raise Sub2ApiError("pricing unavailable")
    def render(items, **kwargs):
        boards.extend(items)
        return b"fake-png"
    monkeypatch.setattr("backend.app.commands.render_sub2_price_image", render)
    router = CommandRouter(Settings(), FakeOneBot(), box, probe=FakeProbe(), sub2_client=BrokenClient())
    assert await router.handle_message(session, IncomingMessage("111", "/price", "group", "123")) == ""
    assert boards[0].rates and not boards[0].model_prices
    assert "获取失败" in boards[0].price_note
    assert config.last_error == "pricing unavailable"


def test_newapi_image_expands_all_models_and_marks_dynamic_request_and_unknown_cache(monkeypatch):
    from backend.app.material_theme import MaterialDraw
    from backend.app.newapi_pricing import flatten_newapi_catalog
    from backend.app.sub2_price_image import Sub2PriceBoard, render_sub2_price_image
    from backend.app.sub2_rates import Sub2StoredRate
    catalog = flatten_newapi_catalog(newapi_pricing_payload())
    rates = [Sub2StoredRate(r.platform, r.group_key, r.group_name, r.rate_multiplier, utc_now(), ()) for r in catalog.rates]
    texts = []
    original = MaterialDraw.__getattr__
    def tracked(self, name):
        function = original(self, name)
        if name != "text":
            return function
        def draw_text(position, value, **kwargs):
            texts.append((position, value))
            return function(position, value, **kwargs)
        return draw_text
    monkeypatch.setattr(MaterialDraw, "__getattr__", tracked)
    image = Image.open(BytesIO(render_sub2_price_image([Sub2PriceBoard("New API 离线价格", rates, model_prices=catalog.model_prices)])))
    values = [value for _, value in texts]
    assert all(model.model_name in values for model in catalog.model_prices)
    assert any("USD / MTok" in value for value in values)
    assert "0.006000 / 次" in values
    assert "动态计费，详见模型广场" in values
    assert "—" in values
    assert max(position[1] for position, _ in texts) + 20 < image.height


@pytest.mark.asyncio
async def test_removed_stat_command_and_legacy_alias_never_send(database):
    from backend.app.command_settings import list_command_settings, set_command_aliases
    from backend.app.commands import CommandRouter, IncomingMessage
    from backend.app.models import BotCommandSetting
    from tests.test_commands import FakeOneBot, FakeProbe
    session, _ = database
    session.add(BotCommandSetting(command="/stat", enabled=False, aliases=["网页快照"]))
    session.commit()
    onebot = FakeOneBot()
    router = CommandRouter(Settings(), onebot, SecretBox("test"), probe=FakeProbe())
    for command in ("/stat", "网页快照"):
        assert await router.handle_message(session, IncomingMessage("123", command, "group", "456")) is None
    assert onebot.sent == []
    assert "/stat" not in {definition.command for definition, _, _ in list_command_settings(session)}
    set_command_aliases(session, "/status", ["网页快照"])


def tibo_timeline_post(post_id="200", text="New release.", **values):
    return {"id": post_id, "text": text, "created_at": "2026-10-19T04:00:00Z",
            "author": {"screen_name": "thsottiaux", "name": "Tibo"}, **values}


def test_tibo_timeline_ignores_pin_order_other_authors_and_reposts():
    from backend.app.x_timeline import parse_timeline
    posts = [tibo_timeline_post("100", "Old pinned post.", created_at="2026-01-01T00:00:00Z"),
             tibo_timeline_post(),
             tibo_timeline_post("300", "Reposted.", is_retweet=True, created_at="2026-10-19T05:00:00Z"),
             tibo_timeline_post("400", "Other author.", author={"screen_name": "someone_else"}, created_at="2026-10-19T06:00:00Z")]
    payload = {"props": {"pageProps": {"timeline": {"entries": [
        {"type": "tweet", "content": {"tweet": post}} for post in posts
    ]}}}}
    page = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></html>'
    assert parse_timeline(page.encode())["url"] == "https://x.com/thsottiaux/status/200"


@pytest.mark.parametrize("atom", [False, True])
def test_tibo_rss_and_atom_keep_full_text_and_select_latest_by_time(atom):
    from backend.app.x_timeline import parse_timeline
    if atom:
        body = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>New release.</title>
        <link href="https://x.com/thsottiaux/status/200"/><published>2026-10-19T04:00:00Z</published>
        <content type="html">&lt;p&gt;New &amp;amp; complete.&lt;br/&gt;Second line.&lt;/p&gt;</content></entry></feed>'''
    else:
        body = '''<rss><channel><item><title>Old pin</title><link>https://x.com/thsottiaux/status/100</link>
        <pubDate>Sun, 18 Oct 2026 12:00:00 GMT</pubDate><description>Old.</description></item>
        <item><title>New release</title><link>https://x.com/thsottiaux/status/200</link>
        <pubDate>Mon, 19 Oct 2026 04:00:00 GMT</pubDate><description><![CDATA[<p>New &amp; complete.<br/>Second line.</p>]]></description></item>
        <item><title>Other account</title><link>https://x.com/another/status/300</link>
        <pubDate>Mon, 19 Oct 2026 05:00:00 GMT</pubDate><description>Other.</description></item></channel></rss>'''
    post = parse_timeline(body.encode())
    assert post["url"] == "https://x.com/thsottiaux/status/200"
    assert post["text"] == "New & complete.\nSecond line."


@pytest.mark.parametrize("body", [b"<html>Sign in</html>", b'{"tibo_presence":{"source_url":"https://x.com/thsottiaux/status/100"}}', b'{"tweets":[]}', b"\xff\xfe"])
def test_tibo_invalid_timelines_never_fall_back_to_old_summary_links(body):
    from backend.app.x_timeline import TimelineError, parse_timeline
    with pytest.raises(TimelineError):
        parse_timeline(body)


@pytest.mark.asyncio
async def test_tibo_official_api_keeps_note_text_reply_context_and_token_private():
    from backend.app.tibo_radar import TiboRadarClient
    calls = []
    def handle(request):
        calls.append(request)
        if request.url.host == "api.x.com":
            assert request.headers["authorization"] == "Bearer local-secret"
            if "/by/username/" in request.url.path:
                return httpx.Response(200, json={"data": {"id": "42", "name": "Tibo", "username": "thsottiaux"}})
            assert request.url.path == "/2/users/42/tweets"
            assert request.url.params["exclude"] == "retweets"
            return httpx.Response(200, json={
                "data": [{"id": "200", "author_id": "42", "text": "Short preview…",
                          "note_tweet": {"text": "Full original text from the latest reply."},
                          "created_at": "2026-10-19T04:00:00Z", "public_metrics": {"like_count": 0},
                          "referenced_tweets": [{"id": "150", "type": "replied_to"}]},
                         {"id": "100", "author_id": "42", "text": "Old pinned post.", "created_at": "2026-01-01T00:00:00Z"}],
                "includes": {"tweets": [{"id": "150", "author_id": "7", "text": "Parent post.", "created_at": "2026-10-19T03:00:00Z"}],
                             "users": [{"id": "7", "username": "parent_user", "name": "Parent"}]},
            })
        assert request.url.host == "translate.googleapis.com"
        assert "authorization" not in request.headers
        return httpx.Response(200, json=[[["最新帖子的完整中文译文。", "original"]]])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        radar = TiboRadarClient(client=client, bearer_token="local-secret")
        first = await radar.fetch()
        second = await radar.fetch()
    assert first.post.source_url == second.post.source_url == "https://x.com/thsottiaux/status/200"
    assert first.post.text == "Full original text from the latest reply."
    assert first.post.translated_zh == "最新帖子的完整中文译文。"
    assert first.post.likes == 0
    assert first.post.parent.username == "parent_user"
    assert not hasattr(first, "presence")
    assert sum("/by/username/" in request.url.path for request in calls) == 1


@pytest.mark.asyncio
async def test_tibo_default_rss_mirror_fallback_and_legacy_source_migration():
    from backend.app.tibo_radar import TiboRadarClient
    hosts = []
    def handle(request):
        hosts.append(request.url.host)
        assert "authorization" not in request.headers
        if request.url.host == "fxtwitter.com":
            return httpx.Response(503)
        if request.url.host == "fixupx.com":
            return httpx.Response(200, json={"tweets": [tibo_timeline_post()]})
        if request.url.host == "api.fxtwitter.com":
            return httpx.Response(403)
        assert request.url.host == "translate.googleapis.com"
        return httpx.Response(200, json=[[["新版本。", "New release."]]])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        report = await TiboRadarClient("https://codexradar.com/current.json", client=client).fetch()
    assert hosts[:2] == ["fxtwitter.com", "fixupx.com"]
    assert "codexradar.com" not in hosts
    assert report.post.text == "New release."
    assert report.post.translated_zh == "新版本。"


@pytest.mark.asyncio
async def test_tibo_google_rate_limit_uses_translation_fallback_with_byte_limits():
    from backend.app.tibo_radar import TiboRadarClient
    pieces = []
    source = "An update on Codex. " * 150
    def handle(request):
        assert "authorization" not in request.headers
        if request.url.host == "translate.googleapis.com":
            return httpx.Response(429)
        assert request.url.host == "api.mymemory.translated.net"
        piece = request.url.params["q"]
        assert len(piece.encode()) <= 480
        assert request.url.params["langpair"] == "en|zh-CN"
        pieces.append(piece)
        return httpx.Response(200, json={"responseStatus": 200, "responseData": {"translatedText": "Codex 最新动态。"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        translated, label = await TiboRadarClient(client=client)._translate(source)
    assert "".join(pieces) == source
    assert translated.count("Codex 最新动态。") == len(pieces)
    assert label == "中文翻译 · 机器翻译"


@pytest.mark.asyncio
async def test_tibo_translation_failure_preserves_original_and_explicit_error():
    from backend.app.tibo_radar import TiboRadarClient
    source = "Complete original text, even when services fail."
    def handle(request):
        if request.url.host == "timeline.example":
            return httpx.Response(200, json={"tweets": [tibo_timeline_post(text=source)]})
        return httpx.Response(503)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        report = await TiboRadarClient("https://timeline.example/rss", client=client).fetch()
    assert report.post.text == source
    assert report.post.translation_label == "中文翻译 · 暂不可用"
    assert report.post.translated_zh == "翻译暂不可用，请查看上方原文。"


@pytest.mark.asyncio
async def test_tibo_all_sources_down_fail_without_returning_a_stale_post():
    from backend.app.tibo_radar import TiboRadarClient, TiboRadarError
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429))) as client:
        with pytest.raises(TiboRadarError, match="无法读取最新帖子"):
            await TiboRadarClient(client=client).fetch()


def test_tibo_image_has_original_translation_and_no_regional_panel(monkeypatch):
    from backend.app.material_theme import MaterialDraw
    from backend.app.tibo_radar import TiboPost, TiboRadarReport, render_tibo_radar_image
    texts = []
    original = MaterialDraw.__getattr__
    def tracked(self, name):
        function = original(self, name)
        if name != "text":
            return function
        def draw_text(position, value, **kwargs):
            texts.append((position, value))
            return function(position, value, **kwargs)
        return draw_text
    monkeypatch.setattr(MaterialDraw, "__getattr__", tracked)
    post = TiboPost("https://x.com/thsottiaux/status/200", "Tibo", "thsottiaux", "New release.",
                    "新版本已发布。", "中文翻译 · 机器翻译", utc_now(), 0, 0, 0, None, None)
    report = TiboRadarReport(utc_now(), "Asia/Shanghai", post)
    image = Image.open(BytesIO(render_tibo_radar_image(report)))
    values = [value for _, value in texts]
    assert "New release." in values and "新版本已发布。" in values
    assert not any(term in value for _, value in texts for term in ("PRESENCE", "概率", "判断依据", "位置", "区域"))
    assert max(position[1] for position, _ in texts) + 20 < image.height


@pytest.mark.asyncio
async def test_tibo_watch_baseline_burst_dedup_and_per_target_restart_retry(database, monkeypatch, approved_delivery_unit):
    from backend.app.app_settings import get_app_setting
    from backend.app.tibo_watch import TiboWatcher, TiboWatchSettings, STATE_KEY, read_watch_state, save_watch_settings
    session, factory = database
    clock = [datetime(2026, 10, 19, 5, tzinfo=timezone.utc)]
    monkeypatch.setattr("backend.app.tibo_watch.utc_now", lambda: clock[0])
    rendered, sent = [], []
    class Client:
        posts = [tibo_timeline_post("100", "Initial baseline.")]
        calls = 0
        async def fetch_posts(self):
            self.calls += 1
            return self.posts
        async def report_for_post(self, raw):
            return raw
    def render(raw):
        rendered.append(raw["id"])
        return b"png"
    monkeypatch.setattr("backend.app.tibo_watch.render_tibo_radar_image", render)
    class Delivery:
        private_failed = False
        async def send_image(self, target, image_bytes, filename):
            sent.append((target.target_type, target.target_id))
            assert filename == "tibo-radar.png" and image_bytes == b"png"
            if target.target_type == "private" and not self.private_failed:
                self.private_failed = True
                return SimpleNamespace(ok=False)
            return SimpleNamespace(ok=True)
    box, client, delivery = SecretBox("watch-test"), Client(), Delivery()
    watcher = TiboWatcher(client, delivery, box)
    await watcher.run_once(factory)
    assert client.calls == 0  # Disabled unless explicitly configured.
    save_watch_settings(session, TiboWatchSettings(enabled=True, targets="G123&P456"))
    await watcher.run_once(factory)
    assert read_watch_state(session).cursor == "100"
    assert rendered == sent == []
    clock[0] += timedelta(seconds=120)
    client.posts = [tibo_timeline_post("300", "Private queued post body."), tibo_timeline_post("200", "Another new post."), *client.posts]
    await watcher.run_once(factory)
    assert rendered == ["200", "300"]
    assert sent == [("group", "123"), ("private", "456"), ("group", "123")]
    state = read_watch_state(session)
    assert [(item.post_id, item.target) for item in state.pending] == [("200", "P456"), ("300", "P456")]
    stored = get_app_setting(session, STATE_KEY)
    assert "Private queued post body" not in stored and "Another new post" not in stored
    # Restart the worker with no new poll due; retry pending private deliveries only.
    restarted = TiboWatcher(client, delivery, box)
    clock[0] += timedelta(seconds=61)
    await restarted.run_once(factory)
    assert sent[-2:] == [("private", "456"), ("private", "456")]
    assert not read_watch_state(session).pending
    clock[0] += timedelta(seconds=120)
    await restarted.run_once(factory)
    assert len(sent) == 5  # Re-reading the same feed sends nothing again.


@pytest.mark.asyncio
async def test_tibo_watch_pause_or_removed_target_during_render_stops_delivery(database, monkeypatch, approved_delivery_unit):
    from backend.app.tibo_watch import TiboWatcher, TiboWatchSettings, read_watch_state, save_watch_settings
    session, factory = database
    box = SecretBox("watch-test")
    clock = [datetime(2026, 10, 19, 5, tzinfo=timezone.utc)]
    monkeypatch.setattr("backend.app.tibo_watch.utc_now", lambda: clock[0])
    save_watch_settings(session, TiboWatchSettings(enabled=True, targets="G123&P456"))
    sent = []
    class Client:
        posts = [tibo_timeline_post("100")]
        async def fetch_posts(self):
            return self.posts
        async def report_for_post(self, raw):
            with factory() as other:
                save_watch_settings(other, TiboWatchSettings(enabled=False, targets="G123&P456"))
            return raw
    class Delivery:
        async def send_image(self, *args):
            sent.append(args)
            return SimpleNamespace(ok=True)
    monkeypatch.setattr("backend.app.tibo_watch.render_tibo_radar_image", lambda raw: b"png")
    client = Client()
    watcher = TiboWatcher(client, Delivery(), box)
    await watcher.run_once(factory)
    clock[0] += timedelta(seconds=120)
    client.posts = [tibo_timeline_post("200")]
    await watcher.run_once(factory)
    assert sent == [] and len(read_watch_state(session).pending) == 2
    with factory() as other:
        save_watch_settings(other, TiboWatchSettings(enabled=True, targets="P456"))
    async def report(raw):
        return raw
    client.report_for_post = report
    await watcher.run_once(factory)
    assert len(sent) == 1 and sent[0][0].target_id == "456"
    assert not read_watch_state(session).pending


@pytest.mark.asyncio
async def test_tibo_watch_fetch_failure_preserves_cursor_and_retry_persistence(database, monkeypatch):
    from backend.app.app_settings import set_app_setting
    from backend.app.tibo_watch import TiboWatcher, TiboWatchSettings, WatchState, STATE_KEY, read_watch_state, save_watch_settings
    session, factory = database
    box = SecretBox("watch-test")
    save_watch_settings(session, TiboWatchSettings(enabled=True, targets="G123"))
    set_app_setting(session, STATE_KEY, WatchState(cursor="100").model_dump_json())
    class BrokenClient:
        async def fetch_posts(self):
            raise RuntimeError("unavailable")
    class NoDelivery:
        async def send_image(self, *args):
            pytest.fail("No new post should be sent")
    await TiboWatcher(BrokenClient(), NoDelivery(), box).run_once(factory)
    state = read_watch_state(session)
    assert state.cursor == "100" and state.last_error and state.last_checked_at


@pytest.mark.asyncio
async def test_tibowatch_admin_command_uses_shared_web_settings_and_switch(database):
    from backend.app.commands import CommandRouter, IncomingMessage
    from backend.app.command_settings import set_command_enabled
    from backend.app.tibo_watch import read_watch_settings, watch_view
    from tests.test_commands import FakeOneBot, FakeProbe
    session, _ = database
    session.add(BotAdmin(qq="999"))
    session.commit()
    router = CommandRouter(Settings(), FakeOneBot(), SecretBox("watch-test"), probe=FakeProbe())
    def message(text, user="999"):
        return IncomingMessage(user, text, "private")
    assert "权限不足" in await router.handle_message(session, message("/tibowatch G123", "111"))
    assert not read_watch_settings(session).enabled
    assert "请先配置" in await router.handle_message(session, message("/tibowatch on"))
    assert "已启用" in await router.handle_message(session, message("/tibowatch g123&P456&G123"))
    assert watch_view(session)["settings"]["targets"] == "G123&P456"
    assert "已停用" in await router.handle_message(session, message("/tibowatch off"))
    assert not read_watch_settings(session).enabled
    assert "已启用" in await router.handle_message(session, message("/tibowatch on"))
    assert "G123&P456" in await router.handle_message(session, message("/tibowatch status"))
    assert "格式错误" in await router.handle_message(session, message("/tibowatch 123"))
    set_command_enabled(session, "/tibowatch", False)
    assert await router.handle_message(session, message("/tibowatch off")) == "该命令已关闭。"
    assert read_watch_settings(session).enabled


def test_tibo_watch_configuration_validation_and_status_never_expose_payload(database):
    from backend.app.tibo_watch import TiboWatchSettings, put_watch, watch_view
    session, _ = database
    for data in ({"enabled": True}, {"targets": "Gbad"}, {"interval_seconds": 5}):
        with pytest.raises(ValueError):
            TiboWatchSettings(**data)
    view = put_watch(TiboWatchSettings(enabled=True, targets="g123&p456"), session)
    assert view["settings"] == {"enabled": True, "targets": "G123&P456", "interval_seconds": 120, "accounts": ["thsottiaux"]}
    assert not view["status"]["initialized"]
    assert "pending" not in watch_view(session)["status"]


def test_news_accounts_validation_catalog_and_legacy_migration(database):
    from backend.app.news_accounts import NEWS_ACCOUNTS, normalize_username, recommended_accounts
    from backend.app.tibo_watch import SETTINGS_KEY, STATE_KEY, TiboWatchSettings, read_watch_settings, read_watch_state, watch_view
    session, _ = database
    handles = [normalize_username(item.username) for item in NEWS_ACCOUNTS]
    assert len(handles) == len(set(handles)) and len(handles) >= 60
    assert {item.category for item in NEWS_ACCOUNTS} == {"公司", "产品", "负责人", "创始人"}
    assert not read_watch_settings(session).enabled
    assert read_watch_settings(session).accounts == recommended_accounts()
    assert TiboWatchSettings(accounts=["@OpenAI", "openai", " ClaudeAI "]).accounts == ["openai", "claudeai"]
    for accounts in (["https://x.com/openai"], ["../secret"], ["a" * 16]):
        with pytest.raises(ValueError):
            TiboWatchSettings(accounts=accounts)
    with pytest.raises(ValueError):
        TiboWatchSettings(enabled=True, targets="G123", accounts=[])
    set_app_setting(session, SETTINGS_KEY, '{"enabled":true,"targets":"G123","interval_seconds":120}')
    set_app_setting(session, STATE_KEY, '{"cursor":"100","last_checked_at":"2026-10-19T04:00:00Z"}')
    assert read_watch_settings(session).accounts == ["thsottiaux"]
    assert read_watch_state(session).accounts["thsottiaux"].cursor == "100"
    assert watch_view(session)["status"]["initialized"]


@pytest.mark.asyncio
async def test_news_client_uses_account_feed_and_custom_template_without_token_leak():
    from backend.app.tibo_radar import TiboRadarClient
    from backend.app.x_timeline import parse_timeline_posts
    seen = []
    async def handle(request):
        seen.append(request)
        assert "authorization" not in request.headers
        body = '<rss><channel><item><link>https://x.com/OpenAI/status/200</link><description>Model announcement.</description></item><item><link>https://x.com/other/status/300</link><description>Not their post.</description></item></channel></rss>'
        return httpx.Response(200, content=body.encode())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        root = TiboRadarClient("https://rss.example/{username}.xml", client=http)
        client = root.for_account("@OpenAI")
        posts = await client.fetch_posts()
        assert posts[0]["url"] == "https://x.com/OpenAI/status/200" and len(posts) == 1
        assert str(seen[0].url) == "https://rss.example/openai.xml"
        assert root.source_url == "https://rss.example/thsottiaux.xml"
        other = TiboRadarClient("https://rss.example/tibo-only.xml", client=http).for_account("ClaudeAI")
        assert other.source_url.startswith("https://fxtwitter.com/claudeai/")
    assert parse_timeline_posts(b'<rss><channel><item><link>https://x.com/OpenAI/status/1</link><title>News</title></item></channel></rss>', "openai")[0]["author"]["name"] != "Tibo"


@pytest.mark.asyncio
async def test_news_accounts_have_independent_cursors_and_failure_isolation(database, monkeypatch, approved_delivery_unit):
    from backend.app.tibo_watch import TiboWatcher, TiboWatchSettings, read_watch_state, save_watch_settings
    session, factory = database
    clock = [datetime(2026, 10, 19, 5, tzinfo=timezone.utc)]
    monkeypatch.setattr("backend.app.tibo_watch.utc_now", lambda: clock[0])
    monkeypatch.setattr("backend.app.tibo_watch.render_tibo_radar_image", lambda raw: raw["text"].encode())
    class Client:
        def __init__(self, username):
            self.username = username
        def for_account(self, username):
            return Client(username)
        async def fetch_posts(self):
            if self.username == "broken":
                raise RuntimeError("Never include upstream errors or secrets in status")
            return timelines[self.username]
        async def report_for_post(self, raw):
            return raw
    class Delivery:
        async def send_image(self, target, image_bytes, filename):
            sent.append((target.target_id, image_bytes, filename))
            return SimpleNamespace(ok=True)
    timelines = {"thsottiaux": [tibo_timeline_post("900")], "openai": [tibo_timeline_post("100")]}
    sent = []
    save_watch_settings(session, TiboWatchSettings(enabled=True, targets="G123", accounts=["thsottiaux", "openai", "broken"]))
    watcher = TiboWatcher(Client("thsottiaux"), Delivery(), SecretBox("test"))
    await watcher.run_once(factory)
    assert not sent
    clock[0] += timedelta(seconds=120)
    timelines["thsottiaux"] = [tibo_timeline_post("950", "Tibo update")]
    timelines["openai"] = [tibo_timeline_post("150", "OpenAI update")]
    await watcher.run_once(factory)
    state = read_watch_state(session)
    assert state.accounts["openai"].cursor == "150" and state.cursor == "950"
    assert [item[1] for item in sent] == [b"OpenAI update", b"Tibo update"]
    assert sent[0][2] == "ai-news-openai.png"
    assert "broken" in state.accounts["broken"].last_error
    assert not state.pending


@pytest.mark.asyncio
async def test_news_removed_subscription_discards_queue_and_readdition_baselines(database, monkeypatch, approved_delivery_unit):
    from backend.app.tibo_watch import TiboWatcher, TiboWatchSettings, read_watch_state, save_watch_settings
    session, factory = database
    clock = [datetime(2026, 10, 19, 5, tzinfo=timezone.utc)]
    monkeypatch.setattr("backend.app.tibo_watch.utc_now", lambda: clock[0])
    monkeypatch.setattr("backend.app.tibo_watch.render_tibo_radar_image", lambda raw: b"png")
    class Client:
        posts = [tibo_timeline_post("100")]
        def for_account(self, username):
            return self
        async def fetch_posts(self):
            return self.posts
        async def report_for_post(self, raw):
            if remove_during_render[0]:
                save_watch_settings(session, TiboWatchSettings(enabled=True, targets="G123", accounts=["openai"]))
            return raw
    class Delivery:
        async def send_image(self, *args):
            sent.append(args)
            return SimpleNamespace(ok=False)
    remove_during_render, sent = [False], []
    client = Client()
    save_watch_settings(session, TiboWatchSettings(enabled=True, targets="G123", accounts=["thsottiaux"]))
    watcher = TiboWatcher(client, Delivery(), SecretBox("test"))
    await watcher.run_once(factory)
    clock[0] += timedelta(seconds=120)
    client.posts = [tibo_timeline_post("200")]
    await watcher.run_once(factory)
    assert len(sent) == 1 and len(read_watch_state(session).pending) == 1
    save_watch_settings(session, TiboWatchSettings(enabled=True, targets="G123", accounts=["openai"]))
    assert not read_watch_state(session).pending and "thsottiaux" not in read_watch_state(session).accounts
    save_watch_settings(session, TiboWatchSettings(enabled=True, targets="G123", accounts=["thsottiaux"]))
    client.posts = [tibo_timeline_post("300")]
    await watcher.run_once(factory)
    assert len(sent) == 1 and read_watch_state(session).cursor == "300"
    # Removal while rendering prevents in-flight work from resurrecting old state.
    clock[0] += timedelta(seconds=120)
    client.posts = [tibo_timeline_post("400")]
    remove_during_render[0] = True
    await watcher.run_once(factory)
    assert len(sent) == 1 and not read_watch_state(session).pending


@pytest.mark.asyncio
async def test_newswatch_admin_account_management_and_command_switch(database):
    from backend.app.commands import CommandRouter, IncomingMessage
    from backend.app.command_settings import set_command_enabled
    from backend.app.tibo_watch import read_watch_settings
    from tests.test_commands import FakeOneBot, FakeProbe
    session, _ = database
    session.add(BotAdmin(qq="999")); session.commit()
    router = CommandRouter(Settings(), FakeOneBot(), SecretBox("test"), probe=FakeProbe())
    async def command(text, user="999"):
        return await router.handle_message(session, IncomingMessage(user, text, "private"))
    assert "权限不足" in await command("/newswatch all", "111")
    assert "订阅账号" in await command("/newswatch all")
    assert "elonmusk" in read_watch_settings(session).accounts
    assert "已停用" in await command("/newswatch add @custom_lab")
    assert "@custom_lab" in await command("/newswatch accounts")
    await command("/newswatch remove @custom_lab")
    assert "custom_lab" not in read_watch_settings(session).accounts
    assert "格式错误" in await command("/newswatch add https://bad.example")
    assert "已启用" in await command("/newswatch G123&P456")
    assert "G123&P456" in await command("/newswatch status")
    set_command_enabled(session, "/newswatch", False)
    assert await command("/newswatch off") == "该命令已关闭。"


@pytest.mark.asyncio
async def test_news_polling_is_batched_and_delivery_work_is_bounded(database, monkeypatch, approved_delivery_unit):
    from backend.app.tibo_watch import TiboWatcher, TiboWatchSettings, read_watch_state, save_watch_settings
    session, factory = database
    clock = [datetime(2026, 10, 19, 5, tzinfo=timezone.utc)]
    monkeypatch.setattr("backend.app.tibo_watch.utc_now", lambda: clock[0])
    monkeypatch.setattr("backend.app.tibo_watch.render_tibo_radar_image", lambda raw: raw["id"].encode())
    accounts = [f"source_{index}" for index in range(13)]
    active, peak, fetched, sent = [0], [0], [], []
    class Client:
        def __init__(self, username="thsottiaux"):
            self.username = username
        def for_account(self, username):
            return Client(username)
        async def fetch_posts(self):
            active[0] += 1; peak[0] = max(peak[0], active[0]); fetched.append(self.username)
            await asyncio.sleep(0)
            active[0] -= 1
            return [tibo_timeline_post(str(100 + round_number[0]))]
        async def report_for_post(self, raw):
            return raw
    class Delivery:
        async def send_image(self, *args):
            sent.append(args)
            return SimpleNamespace(ok=True)
    round_number = [0]
    save_watch_settings(session, TiboWatchSettings(enabled=True, targets="G123", accounts=accounts))
    watcher = TiboWatcher(Client(), Delivery(), SecretBox("test"))
    await watcher.run_once(factory)
    assert len(fetched) == 12 and peak[0] <= 4
    await watcher.run_once(factory)
    assert len(fetched) == 13 and set(fetched) == set(accounts) and not sent
    clock[0] += timedelta(seconds=120); round_number[0] = 1
    await watcher.run_once(factory)
    assert len(sent) == 3 and len(read_watch_state(session).pending) == 9
    # Same numeric IDs in separate accounts remain independent posts.
    assert len({item.account for item in read_watch_state(session).pending}) == 9


def test_news_image_labels_actual_account_without_tibo_brand(monkeypatch):
    from backend.app.material_theme import MaterialDraw
    from backend.app.tibo_radar import TiboPost, TiboRadarReport, render_tibo_radar_image
    texts = []
    original = MaterialDraw.__getattr__
    def tracked(self, name):
        function = original(self, name)
        if name != "text":
            return function
        def draw_text(position, value, **kwargs):
            texts.append(value)
            return function(position, value, **kwargs)
        return draw_text
    monkeypatch.setattr(MaterialDraw, "__getattr__", tracked)
    post = TiboPost("https://x.com/OpenAI/status/200", "OpenAI", "OpenAI", "New feature.", "新功能。",
                    "中文翻译 · 机器翻译", utc_now(), 0, 0, 0, None, None)
    image = Image.open(BytesIO(render_tibo_radar_image(TiboRadarReport(utc_now(), "Asia/Shanghai", post))))
    assert image.width == 1600 and "AI NEWS  /  X POST" in texts
    assert any("@OpenAI" in value for value in texts)
    assert all("TIBO" not in value and "thsottiaux" not in value for value in texts)


@pytest.mark.asyncio
@pytest.mark.parametrize("api_unavailable", [False, True])
async def test_news_account_api_identity_cache_and_bearer_boundary(api_unavailable):
    from backend.app.tibo_radar import TiboRadarClient
    lookups, external = [], []
    async def handle(request):
        if request.url.host == "api.x.com":
            assert request.headers["authorization"] == "Bearer local-only-secret"
            if api_unavailable:
                return httpx.Response(429)
            if "/by/username/" in request.url.path:
                username = request.url.path.rsplit("/", 1)[1]; lookups.append(username)
                return httpx.Response(200, json={"data": {"id": "1" if username == "openai" else "2", "username": username}})
            author_id = request.url.path.split("/")[-2]
            return httpx.Response(200, json={"data": [{"id": "200", "author_id": author_id, "text": "Update"}]})
        external.append(request)
        assert "authorization" not in request.headers
        username = request.url.path.split("/")[1]
        return httpx.Response(200, content=f'<rss><channel><item><link>https://x.com/{username}/status/200</link><title>Update</title></item></channel></rss>'.encode())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        root = TiboRadarClient(client=http, bearer_token="local-only-secret")
        for username in ("openai", "claudeai"):
            client = root.for_account(username)
            for _ in range(2):
                posts = await client.fetch_posts()
                assert posts[0]["author"]["screen_name"] == username
    assert len(external) == (4 if api_unavailable else 0)
    assert lookups == ([] if api_unavailable else ["openai", "claudeai"])


@pytest.mark.parametrize("value,code", [
    ("New GPT model for government election campaigns", "politics"),
    ("GPT: Ｐｏｌｉｔｉｃａｌ debate", "politics"),
    ("poli\u200btical news about GPT", "politics"),
    ("新模型发布。政 治 评论。", "politics"),
    ("新模型发布。政&#x6cbb;评论。", "politics"),
    ("GPT update https://example.test/%2570olitics", "politics"),
    ("AI policy: President Trump announces sanctions", "politics"),
    ("GPT 政府合作新闻", "politics"),
    ("模型功能与台灣政黨", "politics"),
    ("图像模型 NSFW examples", "sexual"),
    ("未成年人裸照", "sexual"),
    ("GPT promotes genocide", "violence"),
    ("GPT self-harm instructions", "violence"),
    ("AI casino gambling offers", "illegal"),
    ("新模型用于电信诈骗", "illegal"),
    ("api_key=sk-abcdefghijklmno", "privacy"),
])
def test_news_local_filter_normalizes_and_blocks_risk(value, code):
    from backend.app.news_filter import ContentBlocked, check_post_payload
    with pytest.raises(ContentBlocked) as caught:
        check_post_payload({"text": value})
    assert caught.value.code == code
    assert value not in str(caught.value)


@pytest.mark.parametrize("value", ["New GPT model API release", "Claude Code improves tool use", "Gemini 图像模型发布，降低 API 价格。", "The API reward cache has a new schema."])
def test_news_local_filter_keeps_benign_technical_messages(value):
    from backend.app.news_filter import check_post_payload
    check_post_payload({"text": value})


def filtered_news_report(**changes):
    from dataclasses import replace
    from backend.app.tibo_radar import TiboPost, TiboRadarReport
    post = TiboPost("https://x.com/OpenAI/status/200", "OpenAI", "OpenAI", "GPT API adds a new tool.", "GPT API 新增工具。",
                    "中文翻译 · 机器翻译", utc_now(), 0, 0, 0, None, None)
    return TiboRadarReport(utc_now(), "Asia/Shanghai", replace(post, **changes))


def configure_news_moderator(session, box):
    set_app_setting(session, "intel.llm", json.dumps({"base_url": "https://review.example/v1", "model": "review-model",
                    "key_cipher": box.encrypt("test-review-key"), "timeout_seconds": 2}))


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict,blocked", [
    ({"allowed": True, "ai_related": True, "confidence": 99, "risk_categories": []}, False),
    ({"allowed": False, "ai_related": True, "confidence": 99, "risk_categories": ["politics"]}, True),
    ({"allowed": True, "ai_related": True, "confidence": 94, "risk_categories": []}, True),
    ({"allowed": True, "ai_related": False, "confidence": 99, "risk_categories": []}, True),
    ({"allowed": True, "ai_related": True, "confidence": 99, "risk_categories": ["uncertain"]}, True),
])
async def test_news_semantic_review_requires_clear_approval(database, verdict, blocked):
    from backend.app.news_filter import ContentBlocked, review_report
    session, _ = database
    box = SecretBox("review-test"); configure_news_moderator(session, box)
    async def handle(request):
        assert request.headers["authorization"] == "Bearer test-review-key"
        body = json.loads(request.content)
        assert body["model"] == "review-model" and "max_tokens" not in body and "max_output_tokens" not in body
        assert body["messages"][0]["role"] == "system" and "untrusted" in body["messages"][0]["content"]
        assert "GPT API" in body["messages"][1]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(verdict)}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        if blocked:
            with pytest.raises(ContentBlocked):
                await review_report(filtered_news_report(), session, box, client=client)
        else:
            await review_report(filtered_news_report(), session, box, client=client)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "http", "redirect", "json", "schema", "string_boolean", "missing_config", "oversize"])
async def test_news_moderation_fails_closed_without_leaking_response(database, mode):
    from backend.app.news_filter import ModerationUnavailable, review_report
    session, _ = database
    box = SecretBox("review-test")
    if mode != "missing_config":
        configure_news_moderator(session, box)
    calls = []
    async def handle(request):
        calls.append(request)
        if mode == "timeout":
            raise httpx.ReadTimeout("private server details")
        if mode == "http":
            return httpx.Response(500, text="private server details")
        if mode == "redirect":
            return httpx.Response(302, headers={"Location": "https://untrusted.example/"})
        if mode == "json":
            return httpx.Response(200, text="private server details")
        if mode == "oversize":
            return httpx.Response(200, content=b"x" * 1_048_577)
        content = {"allowed": True} if mode == "schema" else {"allowed": "true", "ai_related": True, "confidence": 100, "risk_categories": []}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        with pytest.raises(ModerationUnavailable) as caught:
            await review_report(filtered_news_report(), session, box, client=client)
    assert "private server details" not in str(caught.value) and "test-review-key" not in str(caught.value)
    assert len(calls) == (0 if mode == "missing_config" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("part", ["original", "translation", "parent", "comment", "author", "avatar", "no_translation", "parent_translation", "comment_translation", "missing_context"])
async def test_news_all_visible_parts_are_screened_before_model(database, part):
    from backend.app.news_filter import ContentBlocked, review_report
    from backend.app.tibo_radar import TiboContextPost, TiboComment
    changes = {
        "original": {"text": "GPT political campaign"},
        "translation": {"translated_zh": "GPT 政治宣传"},
        "parent": {"parent": TiboContextPost("https://x.com/other/status/1", "User", "other", "government news", "背景信息", utc_now())},
        "comment": {"comments": (TiboComment("User", "other", "new API", "选举评论"),)},
        "author": {"author_name": "Political account"},
        "avatar": {"avatar": b"unreviewed-image"},
        "no_translation": {"translation_label": "中文翻译 · 暂不可用"},
        "parent_translation": {"parent": TiboContextPost("https://x.com/other/status/1", "User", "other", "GPT tools", "翻译暂不可用，请查看原文。", utc_now())},
        "comment_translation": {"comments": (TiboComment("User", "other", "GPT tools", "翻译暂不可用，请查看原文。"),)},
        "missing_context": {"context_complete": False},
    }
    session, _ = database
    with pytest.raises(ContentBlocked):
        await review_report(filtered_news_report(**changes[part]), session, SecretBox("test"))


@pytest.mark.asyncio
async def test_news_client_rejects_before_translation_and_discards_avatar():
    from backend.app.news_filter import ContentBlocked
    from backend.app.tibo_radar import TiboRadarClient
    seen = []
    async def handle(request):
        seen.append(str(request.url))
        if request.url.host == "api.fxtwitter.com":
            return httpx.Response(404)
        if request.url.host == "translate.googleapis.com":
            return httpx.Response(200, json=[[["新 GPT 工具。", "GPT tool"]]])
        pytest.fail("External avatar must not be fetched")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        radar = TiboRadarClient(client=client)
        with pytest.raises(ContentBlocked):
            await radar.report_for_post(tibo_timeline_post("200", "GPT government election campaign", url="https://x.com/thsottiaux/status/200"))
        assert not seen
        raw = tibo_timeline_post("200", "GPT tool", url="https://x.com/thsottiaux/status/200",
                                 author={"screen_name": "thsottiaux", "name": "Tibo", "avatar_url": "https://untrusted.example/avatar.png"})
        assert (await radar.report_for_post(raw)).post.avatar is None


@pytest.mark.asyncio
@pytest.mark.parametrize("semantic_block", [False, True])
async def test_news_watch_filters_blocked_posts_and_continues_safe_delivery(database, monkeypatch, semantic_block):
    from backend.app.news_filter import review_report
    from backend.app.app_settings import get_app_setting
    from backend.app.tibo_watch import TiboWatcher, TiboWatchSettings, read_watch_state, save_watch_settings, STATE_KEY, watch_view
    session, factory = database
    box = SecretBox("review-test"); configure_news_moderator(session, box)
    clock = [datetime(2026, 10, 19, 5, tzinfo=timezone.utc)]
    monkeypatch.setattr("backend.app.tibo_watch.utc_now", lambda: clock[0])
    class Client:
        posts = [tibo_timeline_post("100", "GPT baseline")]
        async def fetch_posts(self):
            return self.posts
        async def report_for_post(self, raw):
            return filtered_news_report(text=raw["text"])
    calls, sent = [], []
    async def handle(request):
        calls.append(request)
        if semantic_block and "audience A" in json.loads(request.content)["messages"][1]["content"]:
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"allowed":false,"ai_related":true,"confidence":99,"risk_categories":["politics"]}'}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"allowed":true,"ai_related":true,"confidence":99,"risk_categories":[]}'}}]})
    class Delivery:
        async def send_image(self, target, image_bytes, filename):
            assert Image.open(BytesIO(image_bytes)).width == 1600
            sent.append(target.target_id)
            return SimpleNamespace(ok=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async def review(report, session, box):
            await review_report(report, session, box, client=http)
        monkeypatch.setattr("backend.app.tibo_watch.review_report", review)
        client = Client(); watcher = TiboWatcher(client, Delivery(), box)
        save_watch_settings(session, TiboWatchSettings(enabled=True, targets="G123&P456"))
        await watcher.run_once(factory)
        clock[0] += timedelta(seconds=120)
        client.posts = [tibo_timeline_post("200", "GPT API note for audience A" if semantic_block else "GPT political election campaign"), tibo_timeline_post("300", "GPT API adds a tool")]
        await watcher.run_once(factory)
    state = read_watch_state(session)
    assert state.cursor == "300" and state.blocked_count == 1 and not state.pending
    assert sent == ["123", "456"] and len(calls) == (2 if semantic_block else 1)
    assert "election campaign" not in get_app_setting(session, STATE_KEY)
    assert watch_view(session)["status"]["last_blocked_reason"] == "政治或公共事务内容"


@pytest.mark.asyncio
async def test_news_existing_pending_content_is_rechecked_and_removed_for_all_targets(database):
    from backend.app.tibo_watch import TiboWatcher, TiboWatchSettings, WatchState, PendingDelivery, STATE_KEY, read_watch_state, save_watch_settings
    session, factory = database
    box = SecretBox("review-test")
    save_watch_settings(session, TiboWatchSettings(enabled=True, targets="G123&P456"))
    unsafe = json.dumps(tibo_timeline_post("200", "GPT political election campaign"))
    state = WatchState(cursor="200", last_checked_at=utc_now(), pending=[PendingDelivery(post_id="200", payload_encrypted=box.encrypt(unsafe), target=target) for target in ["G123", "P456"]])
    set_app_setting(session, STATE_KEY, state.model_dump_json())
    class Never:
        async def send_image(self, *args):
            pytest.fail("Blocked content reached sender")
        async def report_for_post(self, *args):
            pytest.fail("Blocked content reached translator")
    await TiboWatcher(Never(), Never(), box).run_once(factory)
    state = read_watch_state(session)
    assert not state.pending and state.blocked_count == 1


@pytest.mark.asyncio
async def test_news_missing_moderator_keeps_encrypted_work_and_sends_nothing(database, monkeypatch):
    from backend.app.app_settings import get_app_setting
    from backend.app.tibo_watch import TiboWatcher, TiboWatchSettings, WatchState, PendingDelivery, STATE_KEY, read_watch_state, save_watch_settings
    session, factory = database
    box = SecretBox("review-test")
    save_watch_settings(session, TiboWatchSettings(enabled=True, targets="G123"))
    raw = json.dumps(tibo_timeline_post("200", "GPT API adds a tool"))
    state = WatchState(cursor="200", last_checked_at=utc_now(), pending=[PendingDelivery(post_id="200", payload_encrypted=box.encrypt(raw), target="G123")])
    set_app_setting(session, STATE_KEY, state.model_dump_json())
    class Never:
        async def send_image(self, *args):
            pytest.fail("Unreviewed content reached sender")
        async def report_for_post(self, *args):
            pytest.fail("Missing moderator should not call translator")
    await TiboWatcher(Never(), Never(), box).run_once(factory)
    state = read_watch_state(session)
    assert len(state.pending) == 1 and state.pending[0].retry_at and "审核模型" in state.pending[0].last_error
    assert "GPT API adds a tool" not in get_app_setting(session, STATE_KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize("political", [False, True])
async def test_manual_tibo_command_obeys_same_moderation(database, monkeypatch, political):
    from backend.app.commands import CommandRouter, IncomingMessage
    from backend.app.news_filter import review_report
    from tests.test_commands import FakeOneBot, FakeProbe
    session, _ = database
    box = SecretBox("review-test"); configure_news_moderator(session, box)
    session.add(BotAdmin(qq="999")); session.commit()
    class Client:
        async def fetch(self):
            return filtered_news_report(text="GPT political campaign" if political else "GPT API adds a tool")
    async def handle(request):
        assert not political
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"allowed":true,"ai_related":true,"confidence":99,"risk_categories":[]}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async def review(report, session, box):
            await review_report(report, session, box, client=http)
        monkeypatch.setattr("backend.app.news_filter.review_report", review)
        onebot = FakeOneBot()
        router = CommandRouter(Settings(), onebot, box, probe=FakeProbe(), tibo_client=Client())
        reply = await router.handle_message(session, IncomingMessage("999", "/tibo", "private"))
    assert ("内容过滤" in reply and not onebot.sent) if political else (reply == "" and len(onebot.sent) == 1)


def test_emoji_atlas_covers_current_unicode_and_wraps_whole_sequences():
    import emoji
    from backend.app.emoji_text import atlas_index, key_for, text_atoms, text_width
    from backend.app.material_theme import load_font, material_canvas
    from backend.app.tibo_radar import _wrap_paragraphs
    keys = atlas_index()["glyphs"]
    assert all(key_for(value) in keys for value, info in emoji.EMOJI_DATA.items() if info["E"] <= 17)
    text = "👏🏽👩‍💻🇨🇳❤️1️⃣👨‍👩‍👧‍👦⛓️‍💥"
    assert len(list(text_atoms(text))) == 7
    font = load_font(25)
    lines = _wrap_paragraphs(text * 3, font, 90)
    assert "".join(lines) == text * 3
    assert all(text_width(line, font) <= 90 for line in lines)
    canvas, draw = material_canvas((300, 100))
    draw.text((10, 10), text, font=font, fill="#0f172a")
    colors = canvas.crop((10, 10, 215, 50)).getcolors(10000)
    assert colors and len(colors) > 30  # Actual colored glyphs, not monochrome tofu.


def test_emoji_missing_future_asset_has_readable_fallback(monkeypatch):
    from backend.app import emoji_text
    from backend.app.material_theme import load_font
    monkeypatch.setattr(emoji_text, "atlas_index", lambda: {"glyphs": {}})
    assert emoji_text.fallback("❤️") == "[red heart]"
    assert emoji_text.text_width("❤️", load_font(25)) == round(load_font(25).getlength("[red heart]"))


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol,model,fallback", [("openai", "gpt-test", False), ("anthropic", "claude-test", False), ("auto", "claude-test", True), ("auto", "gpt-test", False)])
async def test_news_model_protocols_and_output_cap_rules(protocol, model, fallback):
    from backend.app.news_models import complete
    box = SecretBox("protocol-test")
    config = {"base_url": "https://model.example/v1", "model": model, "key_cipher": box.encrypt("local-key"), "protocol": protocol}
    calls = []
    async def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        if request.url.path.endswith("/messages"):
            assert body["max_tokens"] > 1 and body["system"] == "instructions"
            assert request.headers["x-api-key"] == "local-key" and "authorization" not in request.headers
            if fallback:
                return httpx.Response(404)
            return httpx.Response(200, json={"content": [{"type": "text", "text": "translated"}], "stop_reason": "end_turn"})
        assert not any(key in body for key in ("max_tokens", "max_output_tokens", "max_completion_tokens"))
        assert request.headers["authorization"] == "Bearer local-key" and "x-api-key" not in request.headers
        return httpx.Response(200, json={"choices": [{"message": {"content": "translated"}, "finish_reason": "stop"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        assert await complete(config, box, "instructions", "source", client=client) == "translated"
    assert len(calls) == (2 if fallback else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["valid", "lost_token", "duplicate_token", "invalid_json", "truncated", "network"])
async def test_llm_translation_keeps_handles_urls_emoji_and_fails_closed(mode):
    from backend.app.news_models import translate_text, TranslationUnavailable
    box = SecretBox("translation-test")
    config = {"base_url": "https://model.example/v1", "model": "gpt-test", "key_cipher": box.encrypt("local-key")}
    text = "@superwhisper Meetings is live 🚀👩‍💻\nRead https://example.test/meetings"
    async def handle(request):
        if mode == "network":
            raise httpx.ReadTimeout("private response")
        body = json.loads(request.content)
        source = body["messages"][1]["content"]
        assert "@superwhisper" not in source and "🚀" not in source and "https://example.test" not in source
        translated = source.replace("Meetings is live", "Meetings 已上线").replace("Read", "阅读")
        import re
        token = re.findall(r"__KEEP_\w+__", source)[0]
        if mode == "lost_token":
            translated = translated.replace(token, "")
        elif mode == "duplicate_token":
            translated += token
        value = "bad response" if mode == "invalid_json" else json.dumps({"translation": translated})
        return httpx.Response(200, json={"choices": [{"message": {"content": value}, "finish_reason": "length" if mode == "truncated" else "stop"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        if mode != "valid":
            with pytest.raises(TranslationUnavailable):
                await translate_text(text, config, box, client=client)
        else:
            translated, label = await translate_text(text, config, box, client=client)
            assert translated == "@superwhisper Meetings 已上线 🚀👩‍💻\n阅读 https://example.test/meetings"
            assert "大模型" in label


@pytest.mark.asyncio
async def test_model_commands_select_independent_references_and_respect_permissions(database):
    from backend.app.commands import CommandRouter, IncomingMessage
    from backend.app.command_settings import set_command_enabled
    from backend.app.news_models import resolve_model, model_view, selections
    from backend.app.tibo_watch import read_watch_state
    from tests.test_commands import FakeOneBot, FakeProbe
    session, _ = database
    box = SecretBox("selection-test")
    first = add_config(session, box, name="review model", model_name="claude-review")
    second = add_config(session, box, name="translate model", model_name="gpt-translate")
    session.add(BotAdmin(qq="999")); session.commit()
    router = CommandRouter(Settings(), FakeOneBot(), box, probe=FakeProbe())
    async def command(text, qq="999"):
        return await router.handle_message(session, IncomingMessage(qq, text, "private"))
    assert "权限不足" in await command("/reviewmodel list", "111")
    listing = await command("/reviewmodel list")
    assert "review model" in listing and "sk-test" not in listing and "https://example.com" not in listing
    generation = read_watch_state(session).generation
    assert "claude-review" in await command("/reviewmodel review model")
    assert "gpt-translate" in await command(f"/translatemodel {second.id}")
    assert read_watch_state(session).generation != generation
    assert selections(session) == {"review": first.id, "translation": second.id}
    assert box.decrypt(resolve_model(session, "translation")["key_cipher"]) == "sk-test"
    assert "不能关闭" in await command("/reviewmodel off")
    assert "未找到" in await command("/translatemodel 99999")
    second.model_name = "gpt-updated"; session.commit()
    assert resolve_model(session, "translation")["model"] == "gpt-updated"  # Live reference, not a copied credential snapshot.
    second.enabled = False; session.commit()
    assert not model_view(session, "translation")["available"]
    set_command_enabled(session, "/reviewmodel", False)
    assert await command("/reviewmodel default") == "该命令已关闭。"
    assert "公开机器翻译" in await command("/translatemodel off")


@pytest.mark.asyncio
async def test_translation_scope_is_task_local_and_replaces_public_translator(database, monkeypatch):
    from backend.app.news_models import choose_model, translation_scope, active_translator
    from backend.app.tibo_radar import TiboRadarClient
    session, _ = database
    box = SecretBox("scope-test")
    config = add_config(session, box)
    choose_model(session, "translation", str(config.id))
    async def fake(text, config, box):
        await asyncio.sleep(0)
        return "自然译文", "中文翻译 · 大模型翻译"
    monkeypatch.setattr("backend.app.news_models.translate_text", fake)
    async def scoped():
        with translation_scope(session, box):
            return await TiboRadarClient()._translate("GPT product is live")
    async def isolated():
        await asyncio.sleep(0)
        assert active_translator.get() is None
    result, _ = await asyncio.gather(scoped(), isolated())
    assert result[0] == "自然译文" and active_translator.get() is None


def test_tianyi_is_recommended_and_profile_points_to_correct_handle():
    from backend.app.news_accounts import NEWS_ACCOUNTS
    account = next(item for item in NEWS_ACCOUNTS if item.username == "tianyi")
    assert account.recommended and account.company == "DeepSeek" and account.view()["url"] == "https://x.com/tianyi"


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe_translation", [False, True])
async def test_selected_translation_and_review_models_run_before_delivery(database, monkeypatch, unsafe_translation):
    from backend.app import news_models, news_filter
    from backend.app.news_models import choose_model
    from backend.app.tibo_watch import TiboWatcher, TiboWatchSettings, read_watch_state, save_watch_settings
    from backend.app.tibo_radar import TiboRadarClient
    session, factory = database
    box = SecretBox("selected-flow")
    review = add_config(session, box, name="review", model_name="claude-review")
    review.protocol = "anthropic"; session.commit()
    translate = add_config(session, box, name="translate", model_name="gpt-translate")
    choose_model(session, "review", str(review.id)); choose_model(session, "translation", str(translate.id))
    clock = [datetime(2026, 10, 20, 5, tzinfo=timezone.utc)]
    monkeypatch.setattr("backend.app.tibo_watch.utc_now", lambda: clock[0])
    sent, calls = [], []
    class Client:
        posts = [tibo_timeline_post("100", "GPT API baseline")]
        async def fetch_posts(self):
            return self.posts
        async def report_for_post(self, raw):
            translated, label = await TiboRadarClient()._translate(raw["text"])
            return filtered_news_report(text=raw["text"], translated_zh=translated, translation_label=label)
    class Delivery:
        async def send_image(self, target, image_bytes, filename):
            assert Image.open(BytesIO(image_bytes)).width == 1600
            sent.append(target.target_id)
            return SimpleNamespace(ok=True)
    async def handle(request):
        body = json.loads(request.content); calls.append(body["model"])
        if body["model"] == "gpt-translate":
            value = body["messages"][1]["content"].replace("GPT API is live", "GPT API 已上线")
            if unsafe_translation:
                value += " 政治评论"
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"translation": value})}}]})
        assert request.url.path.endswith("/messages") and "max_tokens" in body
        assert "已上线" in body["messages"][0]["content"] and "大模型翻译" in body["messages"][0]["content"]
        return httpx.Response(200, json={"content": [{"type": "text", "text": '{"allowed":true,"ai_related":true,"confidence":99,"risk_categories":[]}'}], "stop_reason": "end_turn"})
    original = news_models.complete
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async def complete(config, box, system, content, **kwargs):
            return await original(config, box, system, content, client=http)
        monkeypatch.setattr(news_models, "complete", complete)
        monkeypatch.setattr(news_filter, "complete", complete)
        client = Client(); watcher = TiboWatcher(client, Delivery(), box)
        save_watch_settings(session, TiboWatchSettings(enabled=True, targets="G123&P456"))
        await watcher.run_once(factory)
        clock[0] += timedelta(seconds=120)
        client.posts = [tibo_timeline_post("200", "@superwhisper GPT API is live 🚀👩‍💻")]
        await watcher.run_once(factory)
    state = read_watch_state(session)
    assert not state.pending
    assert calls == (["gpt-translate"] if unsafe_translation else ["gpt-translate", "claude-review"])
    assert sent == ([] if unsafe_translation else ["123", "456"])
    assert state.blocked_count == (1 if unsafe_translation else 0)
