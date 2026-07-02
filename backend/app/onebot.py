from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from backend.app.settings import Settings

logger = logging.getLogger(__name__)
OneBotEventHandler = Callable[[dict], Awaitable[None]]


def auth_headers(access_token: str) -> dict[str, str]:
    if not access_token:
        return {}
    return {"Authorization": f"Bearer {access_token}"}


def append_access_token_query(url: str, token: str) -> str:
    if not token:
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["access_token"] = token
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


@dataclass(slots=True)
class OneBotSendResult:
    ok: bool
    payload: dict | None = None
    error: str | None = None
    action: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    message: str | None = None
    status_code: int | None = None


class OneBotClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.onebot_api_base_url)

    async def _post_action(
        self,
        action: str,
        payload: dict,
        target_type: str = "system",
        target_id: str = "",
        message: str = "",
    ) -> OneBotSendResult:
        if not self.settings.onebot_api_base_url:
            return OneBotSendResult(
                ok=False,
                error="ONEBOT_API_BASE_URL is not configured.",
                action=action,
                target_type=target_type,
                target_id=target_id,
                message=message,
            )
        base = self.settings.onebot_api_base_url.rstrip("/")
        url = f"{base}/{action.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=auth_headers(self.settings.onebot_access_token),
                )
            try:
                parsed = response.json() if response.content else {}
            except ValueError:
                parsed = {"raw": response.text[:500]}
            data = parsed if isinstance(parsed, dict) else {"raw": parsed}
            if response.status_code >= 400:
                return OneBotSendResult(
                    ok=False,
                    payload=data,
                    error=f"HTTP {response.status_code}",
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    message=message,
                    status_code=response.status_code,
                )
            if data.get("status") == "failed":
                return OneBotSendResult(
                    ok=False,
                    payload=data,
                    error=str(data.get("wording") or data.get("message") or "OneBot returned status=failed."),
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    message=message,
                    status_code=response.status_code,
                )
            return OneBotSendResult(
                ok=True,
                payload=data,
                action=action,
                target_type=target_type,
                target_id=target_id,
                message=message,
                status_code=response.status_code,
            )
        except Exception as exc:
            logger.warning("OneBot action %s failed: %s", action, exc)
            return OneBotSendResult(
                ok=False,
                error=str(exc),
                action=action,
                target_type=target_type,
                target_id=target_id,
                message=message,
            )

    async def send_group_msg(self, group_id: str, message: str) -> OneBotSendResult:
        return await self._post_action(
            "send_group_msg",
            {"group_id": int(group_id), "message": message},
            target_type="group",
            target_id=str(group_id),
            message=message,
        )

    async def send_private_msg(self, user_id: str, message: str) -> OneBotSendResult:
        return await self._post_action(
            "send_private_msg",
            {"user_id": int(user_id), "message": message},
            target_type="private",
            target_id=str(user_id),
            message=message,
        )

    async def send_message(self, target_type: str, target_id: str, message: str) -> OneBotSendResult:
        if target_type == "group":
            return await self.send_group_msg(target_id, message)
        return await self.send_private_msg(target_id, message)

    async def send_image_message(
        self,
        target_type: str,
        target_id: str,
        image_bytes: bytes,
        filename: str = "status.png",
    ) -> OneBotSendResult:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        message = f"[CQ:image,file=base64://{encoded}]"
        preview = f"[image:{filename}]"
        if target_type == "group":
            return await self._post_action(
                "send_group_msg",
                {"group_id": int(target_id), "message": message},
                target_type="group",
                target_id=str(target_id),
                message=preview,
            )
        return await self._post_action(
            "send_private_msg",
            {"user_id": int(target_id), "message": message},
            target_type="private",
            target_id=str(target_id),
            message=preview,
        )

    async def is_in_group(self, group_id: str) -> bool | None:
        result = await self._post_action("get_group_list", {})
        if not result.ok or not isinstance(result.payload, dict):
            return None
        groups = result.payload.get("data")
        if not isinstance(groups, list):
            return None
        return any(str(item.get("group_id")) == str(group_id) for item in groups if isinstance(item, dict))


class OneBotWebSocketReceiver:
    def __init__(self, settings: Settings, handler: OneBotEventHandler) -> None:
        self.settings = settings
        self.handler = handler
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self.connected = False
        self.last_error: str | None = None

    def start(self) -> None:
        if self._task is None and self.settings.onebot_ws_url:
            self._task = asyncio.create_task(self._run(), name="onebot-ws-receiver")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _connect(self, url: str, headers: dict[str, str]):
        import websockets

        try:
            return websockets.connect(url, additional_headers=headers)
        except TypeError:
            return websockets.connect(url, extra_headers=headers)

    async def _run(self) -> None:
        delay_seconds = 2
        while not self._stopping.is_set():
            url = self.settings.onebot_ws_url
            headers = auth_headers(self.settings.onebot_access_token)
            if self.settings.onebot_ws_token_in_query:
                url = append_access_token_query(url, self.settings.onebot_access_token)
            try:
                async with self._connect(url, headers) as websocket:
                    self.connected = True
                    self.last_error = None
                    async for raw in websocket:
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.debug("Ignored non-JSON OneBot websocket payload.")
                            continue
                        if isinstance(event, dict):
                            await self.handler(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self.last_error = str(exc)
                logger.warning("OneBot websocket disconnected: %s", exc)
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay_seconds)
                except asyncio.TimeoutError:
                    pass
            finally:
                self.connected = False
