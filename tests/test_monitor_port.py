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
from test_monitor import FakeNotifier, SequenceProbe, add_config, add_sub2_config


@pytest.fixture
def database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session, factory
    engine.dispose()


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
