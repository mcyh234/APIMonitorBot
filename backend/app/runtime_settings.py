from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.app_settings import get_app_setting, set_app_setting
from backend.app.crypto import SecretBox
from backend.app.settings import Settings

ONEBOT_WS_URL_KEY = "onebot.ws_url"
ONEBOT_ACCESS_TOKEN_KEY = "onebot.access_token_encrypted"
ONEBOT_WS_TOKEN_IN_QUERY_KEY = "onebot.ws_token_in_query"
NIGHT_SAVER_ENABLED_KEY = "monitor.night_saver_enabled"
NIGHT_SAVER_START_MINUTES_KEY = "monitor.night_saver_start_minutes"
NIGHT_SAVER_END_MINUTES_KEY = "monitor.night_saver_end_minutes"
NIGHT_SAVER_INTERVAL_SECONDS_KEY = "monitor.night_saver_interval_seconds"
COMMAND_COOLDOWN_SECONDS_KEY = "commands.cooldown_seconds"


@dataclass(frozen=True, slots=True)
class OneBotRuntimeSettings:
    ws_url: str
    access_token_configured: bool
    access_token_preview: str | None
    ws_token_in_query: bool


@dataclass(frozen=True, slots=True)
class MonitoringRuntimeSettings:
    night_saver_enabled: bool
    night_saver_start_time: str
    night_saver_end_time: str
    night_saver_interval_minutes: int
    command_cooldown_minutes: int


def apply_runtime_settings(session: Session, settings: Settings, secret_box: SecretBox) -> None:
    ws_url = get_app_setting(session, ONEBOT_WS_URL_KEY)
    if ws_url is not None:
        settings.onebot_ws_url = ws_url

    encrypted_token = get_app_setting(session, ONEBOT_ACCESS_TOKEN_KEY)
    if encrypted_token is not None:
        settings.onebot_access_token = secret_box.decrypt(encrypted_token)

    token_in_query = get_app_setting(session, ONEBOT_WS_TOKEN_IN_QUERY_KEY)
    if token_in_query is not None:
        settings.onebot_ws_token_in_query = token_in_query == "true"
    apply_monitoring_runtime_settings(session, settings)


def apply_monitoring_runtime_settings(session: Session, settings: Settings) -> None:
    enabled = get_app_setting(session, NIGHT_SAVER_ENABLED_KEY)
    if enabled is not None:
        settings.night_saver_enabled = enabled == "true"

    start_minutes = _stored_int(session, NIGHT_SAVER_START_MINUTES_KEY, 0, 1439)
    if start_minutes is not None:
        settings.night_saver_start_hour, settings.night_saver_start_minute = divmod(start_minutes, 60)

    end_minutes = _stored_int(session, NIGHT_SAVER_END_MINUTES_KEY, 0, 1439)
    if end_minutes is not None:
        settings.night_saver_end_hour, settings.night_saver_end_minute = divmod(end_minutes, 60)

    interval_seconds = _stored_int(session, NIGHT_SAVER_INTERVAL_SECONDS_KEY, 60, 86400)
    if interval_seconds is not None:
        settings.night_saver_interval_seconds = interval_seconds

    cooldown_seconds = _stored_int(session, COMMAND_COOLDOWN_SECONDS_KEY, 0, 86400)
    if cooldown_seconds is not None:
        settings.command_check_cooldown_seconds = cooldown_seconds


def current_onebot_runtime_settings(
    session: Session,
    settings: Settings,
    secret_box: SecretBox,
) -> OneBotRuntimeSettings:
    token = settings.onebot_access_token
    encrypted_token = get_app_setting(session, ONEBOT_ACCESS_TOKEN_KEY)
    if encrypted_token is not None:
        token = secret_box.decrypt(encrypted_token)
    return OneBotRuntimeSettings(
        ws_url=get_app_setting(session, ONEBOT_WS_URL_KEY) or settings.onebot_ws_url,
        access_token_configured=bool(token),
        access_token_preview=mask_secret(token) if token else None,
        ws_token_in_query=(
            (get_app_setting(session, ONEBOT_WS_TOKEN_IN_QUERY_KEY) == "true")
            if get_app_setting(session, ONEBOT_WS_TOKEN_IN_QUERY_KEY) is not None
            else settings.onebot_ws_token_in_query
        ),
    )


def save_onebot_runtime_settings(
    session: Session,
    settings: Settings,
    secret_box: SecretBox,
    *,
    ws_url: str,
    access_token: str | None,
    ws_token_in_query: bool,
) -> OneBotRuntimeSettings:
    clean_ws_url = ws_url.strip()
    if clean_ws_url and not clean_ws_url.startswith(("ws://", "wss://")):
        raise ValueError("OneBot WebSocket 地址必须以 ws:// 或 wss:// 开头。")
    set_app_setting(session, ONEBOT_WS_URL_KEY, clean_ws_url)
    set_app_setting(session, ONEBOT_WS_TOKEN_IN_QUERY_KEY, "true" if ws_token_in_query else "false")
    if access_token is not None:
        set_app_setting(session, ONEBOT_ACCESS_TOKEN_KEY, secret_box.encrypt(access_token.strip()))
    apply_runtime_settings(session, settings, secret_box)
    return current_onebot_runtime_settings(session, settings, secret_box)


def current_monitoring_runtime_settings(settings: Settings) -> MonitoringRuntimeSettings:
    return MonitoringRuntimeSettings(
        night_saver_enabled=settings.night_saver_enabled,
        night_saver_start_time=_format_time(settings.night_saver_start_hour, settings.night_saver_start_minute),
        night_saver_end_time=_format_time(settings.night_saver_end_hour, settings.night_saver_end_minute),
        night_saver_interval_minutes=settings.night_saver_interval_seconds // 60,
        command_cooldown_minutes=settings.command_check_cooldown_seconds // 60,
    )


def save_monitoring_runtime_settings(
    session: Session,
    settings: Settings,
    *,
    night_saver_enabled: bool,
    night_saver_start_time: str,
    night_saver_end_time: str,
    night_saver_interval_minutes: int,
    command_cooldown_minutes: int,
) -> MonitoringRuntimeSettings:
    start_minutes = _parse_time(night_saver_start_time)
    end_minutes = _parse_time(night_saver_end_time)
    if not 1 <= night_saver_interval_minutes <= 1440:
        raise ValueError("夜间巡检间隔必须在 1 到 1440 分钟之间。")
    if not 0 <= command_cooldown_minutes <= 1440:
        raise ValueError("命令冷却时间必须在 0 到 1440 分钟之间。")

    set_app_setting(session, NIGHT_SAVER_ENABLED_KEY, "true" if night_saver_enabled else "false")
    set_app_setting(session, NIGHT_SAVER_START_MINUTES_KEY, str(start_minutes))
    set_app_setting(session, NIGHT_SAVER_END_MINUTES_KEY, str(end_minutes))
    set_app_setting(session, NIGHT_SAVER_INTERVAL_SECONDS_KEY, str(night_saver_interval_minutes * 60))
    set_app_setting(session, COMMAND_COOLDOWN_SECONDS_KEY, str(command_cooldown_minutes * 60))
    apply_monitoring_runtime_settings(session, settings)
    return current_monitoring_runtime_settings(settings)


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _stored_int(session: Session, key: str, minimum: int, maximum: int) -> int | None:
    value = get_app_setting(session, key)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if minimum <= parsed <= maximum else None


def _parse_time(value: str) -> int:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError("时间格式必须是 HH:mm。")
    try:
        hour, minute = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("时间格式必须是 HH:mm。") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("时间必须在 00:00 到 23:59 之间。")
    return hour * 60 + minute


def _format_time(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"
