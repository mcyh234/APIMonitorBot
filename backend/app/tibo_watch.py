"""Persistent Tibo polling and per-target delivery retries."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from backend.app.app_settings import get_app_setting, set_app_setting
from backend.app.crypto import SecretBox
from backend.app.db import get_session
from backend.app.notifier import Notifier, NotifyTarget
from backend.app.repository import format_targets, parse_targets
from backend.app.tibo_radar import TiboRadarClient, render_tibo_radar_image
from backend.app.time_utils import utc_now


router = APIRouter(prefix="/api/tibo-watch")
SETTINGS_KEY = "tibo.watch.settings"
STATE_KEY = "tibo.watch.state"


class TiboWatchSettings(BaseModel):
    enabled: bool = False
    targets: str = Field(default="", max_length=512)
    interval_seconds: int = Field(default=120, ge=60, le=3600)

    @field_validator("targets")
    @classmethod
    def normalize_targets(cls, value):
        return format_targets(parse_targets(value)) if value.strip() else ""

    @model_validator(mode="after")
    def require_targets(self):
        if self.enabled and not self.targets:
            raise ValueError("启用监听前请填写通知对象：G群号 或 PQQ号。")
        return self


class PendingDelivery(BaseModel):
    post_id: str
    payload_encrypted: str
    target: str
    attempts: int = 0
    retry_at: datetime | None = None
    last_error: str | None = None


class WatchState(BaseModel):
    cursor: str | None = None
    last_checked_at: datetime | None = None
    last_sent_at: datetime | None = None
    last_error: str | None = None
    pending: list[PendingDelivery] = Field(default_factory=list)


def read_watch_settings(session: Session) -> TiboWatchSettings:
    return TiboWatchSettings.model_validate_json(get_app_setting(session, SETTINGS_KEY) or "{}")


def read_watch_state(session: Session) -> WatchState:
    return WatchState.model_validate_json(get_app_setting(session, STATE_KEY) or "{}")


def save_watch_settings(session: Session, settings: TiboWatchSettings):
    set_app_setting(session, SETTINGS_KEY, settings.model_dump_json())
    return watch_view(session)


def watch_view(session: Session):
    settings, state = read_watch_settings(session), read_watch_state(session)
    active = set(settings.targets.split("&"))
    pending = [item for item in state.pending if item.target in active]
    return {"settings": settings.model_dump(), "status": {
        "initialized": state.cursor is not None, "last_post_id": state.cursor,
        "last_checked_at": state.last_checked_at, "last_sent_at": state.last_sent_at,
        "pending_count": len(pending),
        "last_error": next((item.last_error for item in pending if item.last_error), state.last_error),
    }}


@router.get("")
def get_watch(session: Session = Depends(get_session)):
    return watch_view(session)


@router.put("")
def put_watch(data: TiboWatchSettings, session: Session = Depends(get_session)):
    return save_watch_settings(session, data)


class TiboWatcher:
    def __init__(self, client: TiboRadarClient, notifier: Notifier, box: SecretBox):
        self.client, self.notifier, self.box = client, notifier, box
        self._lock = asyncio.Lock()

    async def run_once(self, session_factory):
        if self._lock.locked():
            return
        async with self._lock:
            with session_factory() as session:
                settings = read_watch_settings(session)
                if not settings.enabled:
                    return
                state, now = read_watch_state(session), utc_now()
                def save():
                    set_app_setting(session, STATE_KEY, state.model_dump_json())
                if state.last_checked_at is None or now - state.last_checked_at >= timedelta(seconds=settings.interval_seconds):
                    state.last_checked_at = now
                    save()
                    try:
                        posts = await self.client.fetch_posts()
                        if not posts:
                            raise ValueError("Empty timeline")
                        newest_id = max(int(post["id"]) for post in posts)
                        if state.cursor is None:
                            # Establish a baseline without replaying old posts.
                            state.cursor = str(newest_id)
                        else:
                            current = read_watch_settings(session)
                            if not current.enabled:
                                return
                            targets = current.targets.split("&")
                            new_posts = sorted((post for post in posts if int(post["id"]) > int(state.cursor)), key=lambda post: int(post["id"]))
                            for post in new_posts:
                                encrypted = self.box.encrypt(json.dumps(post, ensure_ascii=False))
                                state.pending.extend(PendingDelivery(post_id=post["id"], payload_encrypted=encrypted, target=target) for target in targets)
                            # Persist cursor and queued work together before sending.
                            state.cursor = str(max(int(state.cursor), newest_id))
                        state.last_error = None
                    except Exception:
                        state.last_error = "RSS 获取失败，将按检查间隔重试。"
                    save()
                images = {}
                processed_posts = set()
                for item in list(state.pending):
                    current = read_watch_settings(session)
                    if not current.enabled:
                        return
                    if item.target not in current.targets.split("&"):
                        state.pending.remove(item)
                        save()
                        continue
                    if item.retry_at and item.retry_at > utc_now():
                        continue
                    if item.post_id not in processed_posts and len(processed_posts) >= 3:
                        break
                    # Preserve post ordering separately for each notification target.
                    if any(other.target == item.target and int(other.post_id) < int(item.post_id) for other in state.pending):
                        continue
                    processed_posts.add(item.post_id)
                    try:
                        if item.post_id not in images:
                            raw = json.loads(self.box.decrypt(item.payload_encrypted))
                            report = await self.client.report_for_post(raw)
                            images[item.post_id] = await asyncio.to_thread(render_tibo_radar_image, report)
                        # Re-read after rendering so a paused/removed target stays quiet.
                        current = read_watch_settings(session)
                        if not current.enabled:
                            return
                        if item.target not in current.targets.split("&"):
                            state.pending.remove(item)
                            save()
                            continue
                        kind, target_id = parse_targets(item.target)[0]
                        result = await self.notifier.send_image(NotifyTarget(kind, target_id), images[item.post_id], "tibo-radar.png")
                        if not getattr(result, "ok", False):
                            raise RuntimeError("Delivery not acknowledged")
                    except Exception:
                        item.attempts += 1
                        item.last_error = "图片生成或推送失败，将自动重试。"
                        item.retry_at = utc_now() + timedelta(seconds=min(3600, 60 * 2 ** min(item.attempts - 1, 6)))
                    else:
                        state.pending.remove(item)
                        state.last_sent_at = utc_now()
                    save()
