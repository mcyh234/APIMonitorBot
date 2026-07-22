import httpx
import pytest

from backend.app.sub2api import (
    Sub2ApiClient,
    Sub2ApiError,
    flatten_available_catalog,
    flatten_group_rates,
    flatten_newapi_group_rates,
)


class CaptureClient:
    def __init__(self, payload):
        self.payload = payload
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []

    async def get(self, url: str, headers: dict[str, str]):
        self.urls.append(url)
        self.headers.append(headers)
        return httpx.Response(200, json=self.payload)


class RaisingClient:
    async def get(self, url: str, headers: dict[str, str]):
        raise httpx.ConnectError("", request=httpx.Request("GET", url))

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


@pytest.mark.asyncio
async def test_fetch_available_catalog_uses_channels_endpoint():
    client = CaptureClient({"code": 0, "message": "success", "data": []})
    sub2 = Sub2ApiClient(client=client)

    await sub2.fetch_available_catalog("https://pool.example.com", "access-token")

    assert client.urls == ["https://pool.example.com/api/v1/channels/available"]
    assert client.headers == [{"Authorization": "Bearer access-token"}]


def test_flatten_available_catalog_parses_groups_and_per_token_prices():
    catalog = flatten_available_catalog(
        [
            {
                "name": "OpenAI",
                "platforms": [
                    {
                        "platform": "openai",
                        "groups": [{"id": 2, "name": "Plus", "platform": "openai", "rate_multiplier": 0.15}],
                        "supported_models": [
                            {
                                "name": "gpt-5.6-sol",
                                "platform": "openai",
                                "pricing": {
                                    "billing_mode": "token",
                                    "input_price": 0.0000025,
                                    "output_price": 0.000015,
                                    "cache_write_price": 0,
                                    "cache_read_price": 0.00000025,
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    )

    assert [(item.group_key, item.rate_multiplier) for item in catalog.rates] == [("2", 0.15)]
    assert len(catalog.model_prices) == 1
    assert catalog.model_prices[0].input_price == pytest.approx(0.0000025)


@pytest.mark.asyncio
async def test_fetch_rates_request_error_has_readable_message():
    sub2 = Sub2ApiClient(client=RaisingClient())

    with pytest.raises(Sub2ApiError) as exc_info:
        await sub2.fetch_rates("https://pool.example.com", "access-token")

    message = str(exc_info.value)
    assert "渠道请求失败" in message
    assert "ConnectError" in message
    assert "https://pool.example.com/api/v1/groups/available" in message


@pytest.mark.asyncio
async def test_fetch_newapi_rates_uses_session_cookie_and_user_header():
    client = CaptureClient(
        {
            "default": {"ratio": 1, "desc": "默认分组"},
            "auto": {"ratio": "自动", "desc": "自动路由"},
        }
    )
    upstream = Sub2ApiClient(client=client)

    rates = await upstream.fetch_newapi_rates(
        "https://newapi.example.com",
        user_id="42",
        session_cookie="session=abc",
    )

    assert client.urls == ["https://newapi.example.com/api/user/self/groups"]
    assert client.headers == [{"New-Api-User": "42", "Cookie": "session=abc"}]
    assert [(item.group_key, item.group_name, item.rate_multiplier) for item in rates] == [
        ("default", "默认分组", 1.0)
    ]


def test_flatten_newapi_groups_skips_non_numeric_ratios():
    rates = flatten_newapi_group_rates(
        {
            "plus": {"ratio": 0.2, "desc": "Plus 订阅"},
            "auto": {"ratio": "自动", "desc": "自动"},
        }
    )

    assert [(item.platform, item.group_key, item.group_name) for item in rates] == [
        ("newapi", "plus", "Plus 订阅")
    ]
