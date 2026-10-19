"""Persistent multi-account X polling and per-target delivery retries."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session
from sqlalchemy import select

from backend.app.app_settings import get_app_setting, set_app_setting
from backend.app.crypto import SecretBox
from backend.app.db import get_session
from backend.app.notifier import Notifier, NotifyTarget
from backend.app.repository import format_targets, parse_targets
from backend.app.tibo_radar import TiboRadarClient, render_tibo_radar_image
from backend.app.time_utils import utc_now
from backend.app.models import AppSetting
from backend.app.news_accounts import NEWS_ACCOUNTS, normalize_username, recommended_accounts


router = APIRouter(prefix="/api/tibo-watch")
SETTINGS_KEY = "tibo.watch.settings"
STATE_KEY = "tibo.watch.state"


class TiboWatchSettings(BaseModel):
    enabled: bool = False
    targets: str = Field(default="", max_length=512)
    interval_seconds: int = Field(default=120, ge=60, le=3600)
    accounts: list[str] = Field(default_factory=lambda: ["thsottiaux"], max_length=100)

    @field_validator("accounts")
    @classmethod
    def normalize_accounts(cls, value):
        return list(dict.fromkeys(normalize_username(item) for item in value))

    @field_validator("targets")
    @classmethod
    def normalize_targets(cls, value):
        return format_targets(parse_targets(value)) if value.strip() else ""

    @model_validator(mode="after")
    def require_targets(self):
        if self.enabled and not self.targets:
            raise ValueError("启用监听前请填写通知对象：G群号 或 PQQ号。")
        if self.enabled and not self.accounts:
            raise ValueError("启用监听前请至少选择一个 X 账号。")
        return self


class PendingDelivery(BaseModel):
    post_id: str
    payload_encrypted: str
    target: str
    account: str = "thsottiaux"
    attempts: int = 0
    retry_at: datetime | None = None
    last_error: str | None = None


class AccountState(BaseModel):
    cursor: str | None = None
    last_checked_at: datetime | None = None
    last_error: str | None = None


class WatchState(BaseModel):
    cursor: str | None = None
    last_checked_at: datetime | None = None
    last_sent_at: datetime | None = None
    last_error: str | None = None
    pending: list[PendingDelivery] = Field(default_factory=list)
    accounts: dict[str, AccountState] = Field(default_factory=dict)
    generation: str = "legacy"


def read_watch_settings(session: Session) -> TiboWatchSettings:
    stored = get_app_setting(session, SETTINGS_KEY)
    # Existing installations keep their Tibo-only subscription. New installations
    # start with the recommended sources selected, with delivery still disabled.
    return TiboWatchSettings.model_validate_json(stored) if stored else TiboWatchSettings(accounts=recommended_accounts())


def read_watch_state(session: Session) -> WatchState:
    state = WatchState.model_validate_json(get_app_setting(session, STATE_KEY) or "{}")
    if state.cursor is not None and "thsottiaux" not in state.accounts:
        state.accounts["thsottiaux"] = AccountState(cursor=state.cursor, last_checked_at=state.last_checked_at, last_error=state.last_error)
    return state


def save_watch_settings(session: Session, settings: TiboWatchSettings):
    state = read_watch_state(session)
    state.generation = uuid4().hex
    state.accounts = {key: value for key, value in state.accounts.items() if key in settings.accounts}
    state.pending = [item for item in state.pending if item.account in settings.accounts and item.target in settings.targets.split("&")]
    state.cursor = state.accounts.get("thsottiaux", AccountState()).cursor
    # Change settings and invalidate in-flight work in the same transaction.
    for key, value in ((SETTINGS_KEY, settings.model_dump_json()), (STATE_KEY, state.model_dump_json())):
        row = session.scalar(select(AppSetting).where(AppSetting.key == key))
        if row is None:
            session.add(AppSetting(key=key, value=value))
        else:
            row.value = value
    session.commit()
    return watch_view(session)


def watch_view(session: Session):
    settings, state = read_watch_settings(session), read_watch_state(session)
    active = set(settings.targets.split("&"))
    pending = [item for item in state.pending if item.target in active and item.account in settings.accounts]
    accounts = [{"username": username, **state.accounts.get(username, AccountState()).model_dump(),
                 "pending_count": sum(item.account == username for item in pending)} for username in settings.accounts]
    error = next((item["last_error"] for item in accounts if item["last_error"]), None)
    return {"settings": settings.model_dump(), "catalog": [item.view() for item in NEWS_ACCOUNTS], "status": {
        "initialized": bool(accounts) and all(item["cursor"] is not None for item in accounts), "last_post_id": state.cursor,
        "last_checked_at": state.last_checked_at, "last_sent_at": state.last_sent_at,
        "pending_count": len(pending),
        "last_error": next((item.last_error for item in pending if item.last_error), error),
        "accounts": accounts,
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
        self._clients = {"thsottiaux": client}

    def account_client(self, username):
        if username not in self._clients:
            self._clients[username] = self.client.for_account(username)
        return self._clients[username]

    async def run_once(self, session_factory):
        if self._lock.locked():
            return
        async with self._lock:
            with session_factory() as session:
                settings = read_watch_settings(session)
                if not settings.enabled:
                    return
                self._clients = {key: value for key, value in self._clients.items() if key in settings.accounts or key == "thsottiaux"}
                state, now = read_watch_state(session), utc_now()
                def save():
                    if read_watch_state(session).generation != state.generation:
                        return False
                    set_app_setting(session, STATE_KEY, state.model_dump_json())
                    return True
                # Bound each scheduler pass; slow/unavailable accounts must not
                # delay the entire catalog or prevent queued deliveries forever.
                due = [username for username in settings.accounts
                       if username not in state.accounts or state.accounts[username].last_checked_at is None
                       or now - state.accounts[username].last_checked_at >= timedelta(seconds=settings.interval_seconds)]
                due.sort(key=lambda username: state.accounts.get(username, AccountState()).last_checked_at or datetime.min.replace(tzinfo=now.tzinfo))
                due = due[:12]
                if due:
                    state.last_checked_at = now
                    for username in due:
                        state.accounts.setdefault(username, AccountState()).last_checked_at = now
                    if not save():
                        return
                    semaphore = asyncio.Semaphore(4)
                    async def fetch(username):
                        async with semaphore:
                            return await asyncio.wait_for(self.account_client(username).fetch_posts(), timeout=20)
                    results = await asyncio.gather(*(fetch(username) for username in due), return_exceptions=True)
                    if read_watch_state(session).generation != state.generation:
                        return
                    for username, result in zip(due, results):
                        account = state.accounts[username]
                        try:
                            if isinstance(result, BaseException):
                                raise ValueError("Timeline unavailable")
                            posts = result
                            if not posts:
                                raise ValueError("Empty timeline")
                            newest_id = max(int(post["id"]) for post in posts)
                            if account.cursor is not None:
                                new_posts = sorted((post for post in posts if int(post["id"]) > int(account.cursor)), key=lambda post: int(post["id"]))
                                for post in new_posts:
                                    encrypted = self.box.encrypt(json.dumps(post, ensure_ascii=False))
                                    state.pending.extend(PendingDelivery(account=username, post_id=str(post["id"]), payload_encrypted=encrypted, target=target)
                                                         for target in settings.targets.split("&"))
                            account.cursor = str(max(int(account.cursor or "0"), newest_id))
                            account.last_error = None
                        except Exception:
                            account.last_error = f"@{username} 时间线获取失败，将按检查间隔重试。"
                    tibo = state.accounts.get("thsottiaux", AccountState())
                    state.cursor, state.last_error = tibo.cursor, tibo.last_error
                    if not save():  # Cursor and encrypted queue are committed together.
                        return
                state.pending.sort(key=lambda item: (int(item.post_id), item.account, item.target))
                images = {}
                processed_posts = set()
                for item in list(state.pending):
                    current = read_watch_settings(session)
                    if not current.enabled or read_watch_state(session).generation != state.generation:
                        return
                    if item.target not in current.targets.split("&") or item.account not in current.accounts:
                        state.pending.remove(item)
                        save()
                        continue
                    if item.retry_at and item.retry_at > utc_now():
                        continue
                    post_key = (item.account, item.post_id)
                    if post_key not in processed_posts and len(processed_posts) >= 3:
                        break
                    # Preserve post ordering separately for each notification target.
                    if any(other.account == item.account and other.target == item.target and int(other.post_id) < int(item.post_id) for other in state.pending):
                        continue
                    processed_posts.add(post_key)
                    try:
                        if post_key not in images:
                            raw = json.loads(self.box.decrypt(item.payload_encrypted))
                            report = await self.account_client(item.account).report_for_post(raw)
                            images[post_key] = await asyncio.to_thread(render_tibo_radar_image, report)
                        # Re-read after rendering so a paused/removed target stays quiet.
                        current = read_watch_settings(session)
                        if not current.enabled or read_watch_state(session).generation != state.generation:
                            return
                        if item.target not in current.targets.split("&") or item.account not in current.accounts:
                            state.pending.remove(item)
                            save()
                            continue
                        kind, target_id = parse_targets(item.target)[0]
                        filename = "tibo-radar.png" if item.account == "thsottiaux" else f"ai-news-{item.account}.png"
                        result = await self.notifier.send_image(NotifyTarget(kind, target_id), images[post_key], filename)
                        if not getattr(result, "ok", False):
                            raise RuntimeError("Delivery not acknowledged")
                    except Exception:
                        item.attempts += 1
                        item.last_error = "图片生成或推送失败，将自动重试。"
                        item.retry_at = utc_now() + timedelta(seconds=min(3600, 60 * 2 ** min(item.attempts - 1, 6)))
                    else:
                        state.pending.remove(item)
                        state.last_sent_at = utc_now()
                    if not save():
                        return
