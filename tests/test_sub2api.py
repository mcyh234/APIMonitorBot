import httpx
import pytest

from backend.app.sub2api import Sub2ApiClient, flatten_group_rates


class CaptureClient:
    def __init__(self, payload):
        self.payload = payload
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []

    async def get(self, url: str, headers: dict[str, str]):
        self.urls.append(url)
        self.headers.append(headers)
        return httpx.Response(200, json=self.payload)


def test_flatten_group_rates_parses_groups_available_response():
    rows = [
        {
            "id": 2,
            "name": "OpenAi",
            "platform": "openai",
            "rate_multiplier": 0.06,
        },
        {
            "id": 19,
            "name": "ClaudeCode Max20",
            "platform": "anthropic",
            "rate_multiplier": 0.85,
        },
    ]

    rates = flatten_group_rates(rows)

    assert [(item.platform, item.group_key, item.group_name, item.rate_multiplier) for item in rates] == [
        ("anthropic", "19", "ClaudeCode Max20", 0.85),
        ("openai", "2", "OpenAi", 0.06),
    ]


@pytest.mark.asyncio
async def test_fetch_rates_uses_groups_available_endpoint():
    client = CaptureClient({"code": 0, "message": "success", "data": []})
    sub2 = Sub2ApiClient(client=client)

    await sub2.fetch_rates("https://pool.example.com", "access-token")

    assert client.urls == ["https://pool.example.com/api/v1/groups/available"]
    assert client.headers == [{"Authorization": "Bearer access-token"}]
