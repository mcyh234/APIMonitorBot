from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from typing import Any

import httpx
from sqlalchemy.orm import Session

from backend.app.crypto import SecretBox
from backend.app.model_pricing import ModelTokenPrice
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
class NewApiSession:
    cookie: str
    user_id: str


@dataclass(frozen=True, slots=True)
class Sub2ChannelRateSnapshot:
    platform: str
    group_key: str
    group_name: str
    rate_multiplier: float

    @property
    def identity(self) -> tuple[str, str]:
        return (self.platform, self.group_key)


@dataclass(frozen=True, slots=True)
class Sub2AvailableCatalog:
    rates: tuple[Sub2ChannelRateSnapshot, ...]
    model_prices: tuple[ModelTokenPrice, ...]


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
            raise Sub2ApiError(_request_error_message(exc, "登录请求失败")) from exc
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

    async def refresh(self, base_url: str, refresh_token: str) -> Sub2AuthTokens:
        url = normalize_sub2_url(base_url, "/api/v1/auth/refresh")
        try:
            response = await self._post(url, {"refresh_token": refresh_token})
        except httpx.RequestError as exc:
            raise Sub2AuthError(_request_error_message(exc, "刷新登录凭据失败")) from exc
        data = _response_json(response)
        body = data.get("data") if isinstance(data, dict) else None
        if response.status_code >= 400 or not isinstance(data, dict) or data.get("code") != 0 or not isinstance(body, dict):
            raise Sub2AuthError(_api_message(data) or "刷新登录凭据失败。")
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise Sub2AuthError("刷新登录凭据响应缺少 access_token。")
        expires_in = body.get("expires_in")
        expires_at = utc_now() + timedelta(seconds=max(0, int(expires_in) - 60)) if isinstance(expires_in, (int, float)) and expires_in > 0 else None
        next_refresh = body.get("refresh_token")
        return Sub2AuthTokens(token, next_refresh if isinstance(next_refresh, str) else refresh_token, expires_at)

    async def fetch_rates(self, base_url: str, access_token: str) -> list[Sub2ChannelRateSnapshot]:
        url = normalize_sub2_url(base_url, "/api/v1/groups/available")
        try:
            response = await self._get(url, {"Authorization": f"Bearer {access_token}"})
        except httpx.TimeoutException as exc:
            raise Sub2ApiError(str(exc) or "渠道请求超时。") from exc
        except httpx.RequestError as exc:
            raise Sub2ApiError(_request_error_message(exc, "渠道请求失败")) from exc
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

    async def fetch_available_catalog(self, base_url: str, access_token: str) -> Sub2AvailableCatalog:
        url = normalize_sub2_url(base_url, "/api/v1/channels/available")
        try:
            response = await self._get(url, {"Authorization": f"Bearer {access_token}"})
        except httpx.TimeoutException as exc:
            raise Sub2ApiError(str(exc) or "可用渠道请求超时。") from exc
        except httpx.RequestError as exc:
            raise Sub2ApiError(_request_error_message(exc, "可用渠道请求失败")) from exc
        data = _response_json(response)
        if response.status_code in {401, 403}:
            raise Sub2AuthError(_api_message(data) or f"token 已失效：HTTP {response.status_code}")
        if response.status_code >= 400:
            raise Sub2ApiError(_api_message(data) or f"可用渠道请求失败：HTTP {response.status_code}")
        if not isinstance(data, dict) or data.get("code") != 0:
            raise Sub2ApiError(_api_message(data) or "可用渠道请求失败。")
        rows = data.get("data")
        if not isinstance(rows, list):
            raise Sub2ApiError("可用渠道响应 data 不是列表。")
        return flatten_available_catalog(rows)

    async def fetch_available_catalog_with_cached_token(
        self,
        session: Session,
        config: Sub2Config,
        secret_box: SecretBox,
    ) -> Sub2AvailableCatalog:
        if config.upstream_type == "newapi":
            raise Sub2ApiError("NewAPI 不提供 Sub2API 可用渠道定价接口。")
        access_token = _cached_access_token(config, secret_box)
        if access_token:
            try:
                return await self.fetch_available_catalog(config.base_url, access_token)
            except Sub2AuthError:
                pass

        refresh_token = _encrypted_value(config.refresh_token_encrypted, secret_box)
        if refresh_token:
            try:
                tokens = await self.refresh(config.base_url, refresh_token)
                _save_sub2_tokens(session, config, secret_box, tokens)
                return await self.fetch_available_catalog(config.base_url, tokens.access_token)
            except Sub2AuthError:
                pass

        if not config.email or not config.password_encrypted:
            raise Sub2AuthError("尚未配置登录凭据。")
        tokens = await self.login(
            config.base_url,
            config.email,
            secret_box.decrypt(config.password_encrypted),
        )
        _save_sub2_tokens(session, config, secret_box, tokens)
        return await self.fetch_available_catalog(config.base_url, tokens.access_token)

    async def login_newapi(self, base_url: str, username: str, password: str) -> NewApiSession:
        url = normalize_sub2_url(base_url, "/api/user/login")
        try:
            response = await self._post(url, {"username": username, "password": password})
        except httpx.RequestError as exc:
            raise Sub2AuthError(_request_error_message(exc, "NewAPI 登录请求失败")) from exc
        data = _response_json(response)
        body = data.get("data") if isinstance(data, dict) else None
        if response.status_code >= 400 or not isinstance(data, dict) or data.get("success") is not True:
            raise Sub2AuthError(_api_message(data) or f"NewAPI 登录失败：HTTP {response.status_code}")
        if not isinstance(body, dict) or body.get("require_2fa") is True:
            raise Sub2AuthError("NewAPI 登录需要二次验证，当前不支持自动登录。")
        user_id = body.get("id")
        if user_id is None:
            raise Sub2AuthError("NewAPI 登录响应缺少用户 ID。")
        cookie = _cookie_header(response)
        if not cookie:
            raise Sub2AuthError("NewAPI 登录未返回会话 Cookie。")
        return NewApiSession(cookie=cookie, user_id=str(user_id))

    async def fetch_newapi_rates(
        self,
        base_url: str,
        *,
        user_id: str,
        session_cookie: str | None = None,
        access_token: str | None = None,
    ) -> list[Sub2ChannelRateSnapshot]:
        if not session_cookie and not access_token:
            raise Sub2AuthError("NewAPI 尚未配置登录凭据。")
        headers = {"New-Api-User": str(user_id)}
        if session_cookie:
            headers["Cookie"] = session_cookie
        if access_token:
            headers["Authorization"] = access_token
        url = normalize_sub2_url(base_url, "/api/user/self/groups")
        try:
            response = await self._get(url, headers)
        except httpx.RequestError as exc:
            raise Sub2ApiError(_request_error_message(exc, "NewAPI 分组请求失败")) from exc
        data = _response_json(response)
        if response.status_code in {401, 403}:
            raise Sub2AuthError(_api_message(data) or f"NewAPI 登录已失效：HTTP {response.status_code}")
        if response.status_code >= 400:
            raise Sub2ApiError(_api_message(data) or f"NewAPI 分组请求失败：HTTP {response.status_code}")
        groups = data.get("data") if isinstance(data, dict) and "data" in data else data
        if not isinstance(groups, dict):
            raise Sub2ApiError("NewAPI 分组响应不是对象。")
        return flatten_newapi_group_rates(groups)

    async def fetch_rates_with_cached_token(
        self,
        session: Session,
        config: Sub2Config,
        secret_box: SecretBox,
    ) -> list[Sub2ChannelRateSnapshot]:
        if config.upstream_type == "newapi":
            return await self._fetch_newapi_cached(session, config, secret_box)
        if config.upstream_type == "auto":
            try:
                return await self._fetch_sub2_cached(session, config, secret_box)
            except Sub2ApiError:
                rates = await self._fetch_newapi_cached(session, config, secret_box)
                config.upstream_type = "newapi"
                session.commit()
                return rates
        return await self._fetch_sub2_cached(session, config, secret_box)

    async def _fetch_sub2_cached(
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

        refresh_token = _encrypted_value(config.refresh_token_encrypted, secret_box)
        if refresh_token:
            try:
                tokens = await self.refresh(config.base_url, refresh_token)
                _save_sub2_tokens(session, config, secret_box, tokens)
                return await self.fetch_rates(config.base_url, tokens.access_token)
            except Sub2AuthError:
                pass

        if not config.email or not config.password_encrypted:
            raise Sub2AuthError("尚未配置登录凭据。")
        password = secret_box.decrypt(config.password_encrypted)
        tokens = await self.login(config.base_url, config.email, password)
        _save_sub2_tokens(session, config, secret_box, tokens)
        return await self.fetch_rates(config.base_url, tokens.access_token)

    async def _fetch_newapi_cached(
        self,
        session: Session,
        config: Sub2Config,
        secret_box: SecretBox,
    ) -> list[Sub2ChannelRateSnapshot]:
        user_id = config.newapi_user_id
        access_token = _encrypted_value(config.access_token_encrypted, secret_box)
        cookie = _encrypted_value(config.session_cookie_encrypted, secret_box)
        if user_id and (cookie or access_token):
            try:
                return await self.fetch_newapi_rates(
                    config.base_url, user_id=user_id, session_cookie=cookie, access_token=access_token
                )
            except Sub2AuthError:
                pass
        if not config.email or not config.password_encrypted:
            raise Sub2AuthError("尚未配置登录凭据。")
        login = await self.login_newapi(config.base_url, config.email, secret_box.decrypt(config.password_encrypted))
        config.newapi_user_id = login.user_id
        config.session_cookie_encrypted = secret_box.encrypt(login.cookie)
        config.credential_mode = "password"
        session.commit()
        return await self.fetch_newapi_rates(config.base_url, user_id=login.user_id, session_cookie=login.cookie)

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


def flatten_available_catalog(rows: list[Any]) -> Sub2AvailableCatalog:
    rates: dict[tuple[str, str], Sub2ChannelRateSnapshot] = {}
    prices: dict[tuple[str, str], ModelTokenPrice] = {}
    for channel in rows:
        if not isinstance(channel, dict):
            continue
        platforms = channel.get("platforms")
        if not isinstance(platforms, list):
            continue
        for platform_row in platforms:
            if not isinstance(platform_row, dict):
                continue
            platform = str(platform_row.get("platform") or "unknown").strip().lower()
            groups = platform_row.get("groups")
            if isinstance(groups, list):
                for group in flatten_group_rates(groups):
                    normalized = Sub2ChannelRateSnapshot(
                        platform=platform if platform != "unknown" else group.platform,
                        group_key=group.group_key,
                        group_name=group.group_name,
                        rate_multiplier=group.rate_multiplier,
                    )
                    rates[normalized.identity] = normalized

            models = platform_row.get("supported_models")
            if not isinstance(models, list):
                continue
            for model in models:
                parsed = _model_token_price(model, platform)
                if parsed is not None:
                    prices[(parsed.platform, parsed.model_name.casefold())] = parsed
    return Sub2AvailableCatalog(
        rates=tuple(sorted(rates.values(), key=lambda item: (item.platform, item.group_name.casefold()))),
        model_prices=tuple(sorted(prices.values(), key=lambda item: (item.platform, item.model_name.casefold()))),
    )


def _model_token_price(raw: Any, fallback_platform: str) -> ModelTokenPrice | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    pricing = raw.get("pricing")
    if not name or not isinstance(pricing, dict) or pricing.get("billing_mode") != "token":
        return None

    values: list[float] = []
    for key in ("input_price", "output_price", "cache_write_price", "cache_read_price"):
        value = pricing.get(key, 0)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            return None
        values.append(float(value))
    platform = str(raw.get("platform") or fallback_platform or "unknown").strip().lower()
    return ModelTokenPrice(
        model_name=name,
        platform=platform,
        input_price=values[0],
        output_price=values[1],
        cache_write_price=values[2],
        cache_read_price=values[3],
    )


def flatten_newapi_group_rates(rows: dict[str, Any]) -> list[Sub2ChannelRateSnapshot]:
    snapshots: list[Sub2ChannelRateSnapshot] = []
    for key, group in rows.items():
        if not isinstance(group, dict):
            continue
        rate = group.get("ratio")
        if not isinstance(rate, (int, float)):
            continue
        group_key = str(key).strip()
        if not group_key:
            continue
        name = str(group.get("desc") or group_key).strip() or group_key
        snapshots.append(Sub2ChannelRateSnapshot("newapi", group_key, name, float(rate)))
    snapshots.sort(key=lambda item: item.group_name.casefold())
    return snapshots


def _cached_access_token(config: Sub2Config, secret_box: SecretBox) -> str | None:
    if not config.access_token_encrypted:
        return None
    if config.token_expires_at and coerce_aware_utc(config.token_expires_at) <= utc_now():
        return None
    try:
        return secret_box.decrypt(config.access_token_encrypted)
    except Exception:
        return None


def _encrypted_value(value: str | None, secret_box: SecretBox) -> str | None:
    if not value:
        return None
    try:
        return secret_box.decrypt(value)
    except Exception:
        return None


def _save_sub2_tokens(session: Session, config: Sub2Config, secret_box: SecretBox, tokens: Sub2AuthTokens) -> None:
    config.access_token_encrypted = secret_box.encrypt(tokens.access_token)
    config.refresh_token_encrypted = secret_box.encrypt(tokens.refresh_token) if tokens.refresh_token else None
    config.token_expires_at = tokens.expires_at
    config.upstream_type = "sub2api"
    config.credential_mode = "password"
    session.commit()


def _cookie_header(response: httpx.Response) -> str | None:
    parts = [f"{name}={value}" for name, value in response.cookies.items()]
    return "; ".join(parts) or None


def _response_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise Sub2ApiError("响应不是 JSON。") from exc


def _request_error_message(exc: httpx.RequestError, prefix: str) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    request = getattr(exc, "request", None)
    url = str(request.url) if request is not None else ""
    if url and url not in detail:
        detail = f"{detail} ({url})"
    return f"{prefix}：{detail}"


def _api_message(data: Any) -> str | None:
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        error = data.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()
    return None
