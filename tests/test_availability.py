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
