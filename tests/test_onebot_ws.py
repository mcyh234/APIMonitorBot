import asyncio

import pytest

from backend.app.onebot import OneBotClient
from backend.app.settings import Settings


class FakeReceiver:
    connected = True

    async def call_action(self, action: str, params: dict, timeout_seconds: float):
        self.action = action
        self.params = params
        self.timeout_seconds = timeout_seconds
        return {"status": "ok", "retcode": 0, "data": {"message_id": 1}}


class TimeoutReceiver:
    connected = True

    async def call_action(self, action: str, params: dict, timeout_seconds: float):
        raise asyncio.TimeoutError


@pytest.mark.asyncio
async def test_onebot_client_uses_connected_websocket_for_send():
    client = OneBotClient(
        Settings(
            onebot_ws_url="ws://127.0.0.1:3001",
            onebot_api_base_url="",
            onebot_action_timeout_seconds=1.25,
        )
    )
    receiver = FakeReceiver()
    client.websocket_receiver = receiver

    result = await client.send_group_msg("123456", "hi")

    assert result.ok is True
    assert receiver.action == "send_group_msg"
    assert receiver.params == {"group_id": 123456, "message": "hi"}
    assert receiver.timeout_seconds == 1.25


@pytest.mark.asyncio
async def test_onebot_client_does_not_fallback_to_http_sender():
    client = OneBotClient(
        Settings(
            onebot_ws_url="ws://127.0.0.1:3001",
            onebot_api_base_url="http://127.0.0.1:5700",
            request_timeout_seconds=0.01,
        )
    )

    result = await client.send_group_msg("123456", "hi")

    assert result.ok is False
    assert result.error == "OneBot WebSocket is not connected. HTTP OneBot sending is deprecated."


@pytest.mark.asyncio
async def test_onebot_client_treats_websocket_action_timeout_as_soft_success():
    client = OneBotClient(
        Settings(onebot_ws_url="ws://127.0.0.1:3001", onebot_action_timeout_seconds=1.5)
    )
    client.websocket_receiver = TimeoutReceiver()

    result = await client.send_group_msg("123456", "hi")

    assert result.ok is True
    assert result.error is None
    assert result.payload == {
        "ack_timeout": True,
        "timeout_seconds": 1.5,
        "message": "OneBot WebSocket action sent, but echo response timed out.",
    }
