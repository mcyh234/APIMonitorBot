from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True)
class CheckResult:
    ok: bool
    code: str
    error: str | None = None
    latency_ms: int | None = None
    response_preview: str | None = None
    model_switched: bool = False


@dataclass(slots=True)
class ConnectivityResult:
    ok: bool
    code: str
    error: str | None = None


def normalize_chat_completions_url(base_url: str) -> str:
    clean = base_url.strip()
    if not clean:
        raise ValueError("BaseURL cannot be empty.")
    clean = clean.rstrip("/")
    if clean.endswith("/chat/completions"):
        return clean
    return f"{clean}/chat/completions"


def assistant_content_from_response(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    return ""


def error_message_from_payload(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        code = error.get("code")
        if code is not None:
            return str(code)
    if isinstance(error, str):
        return error.strip()
    return None


class ApiProbe:
    def __init__(
        self,
        timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = False
        self._client = client

    async def probe(self, base_url: str, api_key: str, model_name: str) -> CheckResult:
        try:
            url = normalize_chat_completions_url(base_url)
        except ValueError as exc:
            return CheckResult(ok=False, code="INVALID_BASE_URL", error=str(exc))

        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        start = time.perf_counter()
        try:
            if self._client is not None:
                response = await self._client.post(url, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self.timeout_seconds, verify=self.verify_ssl) as client:
                    response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            return CheckResult(ok=False, code="TIMEOUT", error=str(exc) or "Request timed out.")
        except httpx.RequestError as exc:
            return CheckResult(ok=False, code="NETWORK_ERROR", error=str(exc))

        latency_ms = int((time.perf_counter() - start) * 1000)
        code = str(response.status_code)
        try:
            data = response.json()
        except ValueError:
            data = None

        if not 200 <= response.status_code < 300:
            error = error_message_from_payload(data) or response.text[:240]
            return CheckResult(ok=False, code=code, error=error, latency_ms=latency_ms)

        if not isinstance(data, dict):
            return CheckResult(ok=False, code="INVALID_JSON", error="Response is not JSON.", latency_ms=latency_ms)

        content = assistant_content_from_response(data)
        if not content:
            return CheckResult(
                ok=False,
                code="EMPTY_ASSISTANT_CONTENT",
                error="No assistant content in response.",
                latency_ms=latency_ms,
            )
        return CheckResult(ok=True, code=code, latency_ms=latency_ms, response_preview=content[:240])


class InternetConnectivityProbe:
    def __init__(
        self,
        url: str = "https://www.google.com/generate_204",
        timeout_seconds: float = 8.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = False
        self._client = client

    async def check(self) -> ConnectivityResult:
        try:
            if self._client is not None:
                response = await self._client.get(self.url)
            else:
                async with httpx.AsyncClient(
                    timeout=self.timeout_seconds,
                    verify=self.verify_ssl,
                    follow_redirects=True,
                ) as client:
                    response = await client.get(self.url)
        except httpx.TimeoutException as exc:
            return ConnectivityResult(ok=False, code="TIMEOUT", error=str(exc) or "Request timed out.")
        except httpx.RequestError as exc:
            return ConnectivityResult(ok=False, code="NETWORK_ERROR", error=str(exc))

        if response.status_code < 500:
            return ConnectivityResult(ok=True, code=str(response.status_code))
        return ConnectivityResult(
            ok=False,
            code=str(response.status_code),
            error=response.text[:240],
        )
