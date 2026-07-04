from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.app_settings import get_app_setting, set_app_setting
from backend.app.crypto import SecretBox
from backend.app.settings import Settings

ONEBOT_WS_URL_KEY = "onebot.ws_url"
ONEBOT_ACCESS_TOKEN_KEY = "onebot.access_token_encrypted"
ONEBOT_WS_TOKEN_IN_QUERY_KEY = "onebot.ws_token_in_query"


@dataclass(frozen=True, slots=True)
class OneBotRuntimeSettings:
    ws_url: str
    access_token_configured: bool
    access_token_preview: str | None
    ws_token_in_query: bool


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


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
