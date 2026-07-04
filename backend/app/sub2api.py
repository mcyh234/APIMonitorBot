from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from backend.app.crypto import SecretBox
from backend.app.models import Sub2Config
from backend.app.time_utils import coerce_aware_utc, utc_now


class Sub2ApiError(RuntimeError):
    pass


class Sub2AuthError(Sub2ApiError):
    pass


@dataclass(frozen=True, slots=True)
class Sub2AuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class Sub2ChannelRateSnapshot:
    platform: str
    group_key: str
    group_name: str
    rate_multiplier: float

    @property
    def identity(self) -> tuple[str, str]:
        return (self.platform, self.group_key)


def normalize_sub2_url(base_url: str, path: str) -> str:
    clean = base_url.strip().rstrip("/")
    if not clean:
        raise Sub2ApiError("Sub2API BaseURL 不能为空。")
    if not clean.startswith(("http://", "https://")):
        raise Sub2ApiError("Sub2API BaseURL 必须以 http:// 或 https:// 开头。")
    return f"{clean}/{path.lstrip('/')}"


def format_rate(value: float) -> str:
    return f"{value:g}x"


def platform_label(platform: str) -> str:
    clean = platform.strip().lower()
    if clean == "openai":
        return "OpenAI"
    if clean == "anthropic":
        return "Anthropic"
    return platform.strip() or "Unknown"


class Sub2ApiClient:
    def __init__(self, timeout_seconds: float = 20.0, client: httpx.AsyncClient | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = False
        self._client = client

    async def login(self, base_url: str, email: str, password: str) -> Sub2AuthTokens:
        url = normalize_sub2_url(base_url, "/api/v1/auth/login")
        payload = {"email": email, "password": password}
        try:
            response = await self._post(url, payload)
        except httpx.TimeoutException as exc:
            raise Sub2ApiError(str(exc) or "登录请求超时。") from exc
        except httpx.RequestError as exc:
            raise Sub2ApiError(str(exc)) from exc
        data = _response_json(response)
        if response.status_code >= 400:
            raise Sub2AuthError(_api_message(data) or f"登录失败：HTTP {response.status_code}")
        if not isinstance(data, dict) or data.get("code") != 0:
            raise Sub2AuthError(_api_message(data) or "登录失败。")
        body = data.get("data")
        if not isinstance(body, dict):
            raise Sub2AuthError("登录响应缺少 data。")
        access_token = body.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise Sub2AuthError("登录响应缺少 access_token。")
        refresh_token = body.get("refresh_token")
        expires_in = body.get("expires_in")
        expires_at = None
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            expires_at = utc_now() + timedelta(seconds=max(0, int(expires_in) - 60))
        return Sub2AuthTokens(
            access_token=access_token,
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
            expires_at=expires_at,
        )

    async def fetch_rates(self, base_url: str, access_token: str) -> list[Sub2ChannelRateSnapshot]:
        url = normalize_sub2_url(base_url, "/api/v1/groups/available")
        try:
            response = await self._get(url, {"Authorization": f"Bearer {access_token}"})
        except httpx.TimeoutException as exc:
            raise Sub2ApiError(str(exc) or "渠道请求超时。") from exc
        except httpx.RequestError as exc:
            raise Sub2ApiError(str(exc)) from exc
        data = _response_json(response)
        if response.status_code in {401, 403}:
            raise Sub2AuthError(_api_message(data) or f"token 已失效：HTTP {response.status_code}")
        if response.status_code >= 400:
            raise Sub2ApiError(_api_message(data) or f"渠道请求失败：HTTP {response.status_code}")
        if not isinstance(data, dict) or data.get("code") != 0:
            raise Sub2ApiError(_api_message(data) or "渠道请求失败。")
        rows = data.get("data")
        if not isinstance(rows, list):
            raise Sub2ApiError("渠道响应 data 不是列表。")
        return flatten_group_rates(rows)

    async def fetch_rates_with_cached_token(
        self,
        session: Session,
        config: Sub2Config,
        secret_box: SecretBox,
    ) -> list[Sub2ChannelRateSnapshot]:
        access_token = _cached_access_token(config, secret_box)
        if access_token:
            try:
                return await self.fetch_rates(config.base_url, access_token)
            except Sub2AuthError:
                pass

        password = secret_box.decrypt(config.password_encrypted)
        tokens = await self.login(config.base_url, config.email, password)
        config.access_token_encrypted = secret_box.encrypt(tokens.access_token)
        config.refresh_token_encrypted = (
            secret_box.encrypt(tokens.refresh_token) if tokens.refresh_token else None
        )
        config.token_expires_at = tokens.expires_at
        session.commit()
        return await self.fetch_rates(config.base_url, tokens.access_token)

    async def _post(self, url: str, payload: dict[str, Any]) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(url, json=payload)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, verify=self.verify_ssl) as client:
            return await client.post(url, json=payload)

    async def _get(self, url: str, headers: dict[str, str]) -> httpx.Response:
        if self._client is not None:
            return await self._client.get(url, headers=headers)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, verify=self.verify_ssl) as client:
            return await client.get(url, headers=headers)


def flatten_group_rates(rows: list[Any]) -> list[Sub2ChannelRateSnapshot]:
    snapshots: list[Sub2ChannelRateSnapshot] = []
    for group in rows:
        if not isinstance(group, dict):
            continue
        name = str(group.get("name") or "").strip()
        if not name:
            continue
        rate = group.get("rate_multiplier")
        if not isinstance(rate, (int, float)):
            continue
        group_id = group.get("id")
        group_key = str(group_id) if group_id is not None else name
        platform = str(group.get("platform") or "unknown").strip().lower()
        snapshots.append(
            Sub2ChannelRateSnapshot(
                platform=platform,
                group_key=group_key,
                group_name=name,
                rate_multiplier=float(rate),
            )
        )
    snapshots.sort(key=lambda item: (platform_label(item.platform), item.group_name))
    return snapshots


def flatten_channel_rates(rows: list[Any]) -> list[Sub2ChannelRateSnapshot]:
    return flatten_group_rates(rows)


def _cached_access_token(config: Sub2Config, secret_box: SecretBox) -> str | None:
    if not config.access_token_encrypted:
        return None
    if config.token_expires_at and coerce_aware_utc(config.token_expires_at) <= utc_now():
        return None
    try:
        return secret_box.decrypt(config.access_token_encrypted)
    except Exception:
        return None


def _response_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise Sub2ApiError("响应不是 JSON。") from exc


def _api_message(data: Any) -> str | None:
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        error = data.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()
    return None
