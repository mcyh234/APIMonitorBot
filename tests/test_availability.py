import httpx
import pytest

from backend.app.availability import (
    ApiProbe,
    assistant_content_from_response,
    normalize_chat_completions_url,
)


def test_normalize_chat_completions_url_appends_path():
    assert normalize_chat_completions_url("https://example.com/v1/") == "https://example.com/v1/chat/completions"


def test_normalize_chat_completions_url_keeps_full_endpoint():
    assert (
        normalize_chat_completions_url("https://example.com/v1/chat/completions")
        == "https://example.com/v1/chat/completions"
    )


def test_assistant_content_from_string_response():
    assert (
        assistant_content_from_response({"choices": [{"message": {"content": " hello "}}]})
        == "hello"
    )


def test_assistant_content_from_multimodal_response():
    assert (
        assistant_content_from_response(
            {"choices": [{"message": {"content": [{"type": "text", "text": "hi"}]}}]}
        )
        == "hi"
    )


def test_api_probe_disables_ssl_verification():
    assert ApiProbe().verify_ssl is False


class CapturePostClient:
    def __init__(self):
        self.payload = None

    async def post(self, url: str, json: dict, headers: dict):
        self.payload = json
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})


@pytest.mark.asyncio
async def test_api_probe_does_not_send_output_token_limit_parameters():
    client = CapturePostClient()
    probe = ApiProbe(client=client)

    result = await probe.probe("https://example.com/v1", "sk-test", "gpt-test")

    assert result.ok is True
    assert client.payload is not None
    assert "max_tokens" not in client.payload
    assert "max_output_tokens" not in client.payload
