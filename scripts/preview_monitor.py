"""Isolated, offline UI preview. Never starts the scheduler or OneBot receiver."""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from datetime import timedelta
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PREVIEW = ROOT / "data" / "offline-preview"
PREVIEW.mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_URL"] = "sqlite:///" + (PREVIEW / "preview.sqlite3").as_posix()
os.environ["SECRET_MASTER_KEY"] = "offline-preview-no-real-secrets"

from backend.app import db
from backend.app.app_settings import set_app_setting
from backend.app.availability import CheckResult
from backend.app.crypto import get_secret_box
from backend.app.main import create_app
from backend.app.models import APIConfig, CheckRecord, ProbeObservation, Sub2ChannelRate, Sub2Config, Sub2RateHistory
from backend.app.monitor import MonitorService
from backend.app.monitor_extensions import PublicRule, PublicSettings
from backend.app.notifier import Notifier
from backend.app.onebot import OneBotClient
from backend.app.settings import Settings
from backend.app.time_utils import utc_now
from backend.app.webui_auth import set_webui_secret, webui_secret_configured
from sqlalchemy import select
import uvicorn


class OfflineProbe:
    async def probe(self, *args, **kwargs):
        return CheckResult(True, "200", latency_ms=320)


settings = Settings(_env_file=None, app_name="APIMonitorBot · Offline Preview", database_url=os.environ["DATABASE_URL"],
                    secret_master_key=os.environ["SECRET_MASTER_KEY"], checker_enabled=False,
                    onebot_ws_url="", onebot_access_token="")
db.init_db(settings)
box = get_secret_box()
with db.SessionLocal() as session:
    if not webui_secret_configured(session):
        set_webui_secret(session, "preview-monitor-2026")
    if not session.scalar(select(APIConfig.id)):
        now = utc_now()
        for name, model, protocol in [("主线路", "claude-sonnet", "anthropic"), ("备用线路", "gpt-5.4", "openai")]:
            config = APIConfig(name=name, target_type="group", target_id="123456", base_url="https://offline.invalid/v1",
                api_key_encrypted=box.encrypt("offline"), model_name=model, protocol=protocol, status="ok", last_code="200",
                last_checked_at=now, last_latency_ms=420)
            session.add(config)
            session.flush()
            for i in range(30):
                session.add(CheckRecord(api_config_id=config.id, checked_at=now - timedelta(minutes=29 - i),
                    status="down" if i == 12 else "ok", code="503" if i == 12 else "200",
                    latency_ms=400 + (i % 4) * 80, scheduled=True, model_switched=i == 15))
            session.add(ProbeObservation(api_config_id=config.id, checked_at=now - timedelta(minutes=4), latency_ms=20000))
        sub = Sub2Config(name="示例上游", target_type="group", target_id="123456", base_url="https://offline.invalid",
            email="offline@example.invalid", password_encrypted=box.encrypt("offline"), enabled=True, last_checked_at=now)
        session.add(sub)
        session.flush()
        session.add(Sub2ChannelRate(sub2_config_id=sub.id, platform="openai", group_key="plus", group_name="Plus 标准分组", rate_multiplier=.15, last_seen_at=now))
        for i in range(5):
            session.add(Sub2RateHistory(sub2_config_id=sub.id, platform="openai", group_key="plus", group_name="Plus 标准分组",
                rate_multiplier=.1 + i * .01, recorded_at=now - timedelta(days=4 - i)))
        session.commit()
        set_app_setting(session, "monitor.public", PublicSettings(enabled=True, rules=[
            PublicRule(config_id=sub.id, platform="openai", group_key="plus", visible=True)]).model_dump_json())

app = create_app(settings)


@asynccontextmanager
async def offline_lifespan(app):
    app.state.settings = settings
    app.state.onebot = OneBotClient(settings)
    app.state.monitor = MonitorService(settings, box, Notifier(), probe=OfflineProbe())
    yield


app.router.lifespan_context = offline_lifespan

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    uvicorn.run(app, host="127.0.0.1", port=parser.parse_args().port)
