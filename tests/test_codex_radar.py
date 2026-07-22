from datetime import datetime
from io import BytesIO

import httpx
import pytest
from PIL import Image

from backend.app.codex_radar import (
    CodexRadarClient,
    parse_codex_radar_payload,
    render_codex_radar_image,
)


def sample_payload():
    point = {
        "date": "2026-07-13-pm",
        "score": 105,
        "passed": 7,
        "tasks": 10,
        "cost_usd": 18.94,
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
    }
    return {
        "monitored_at": "2026-07-13T22:08:07+08:00",
        "timezone": "Asia/Shanghai",
        "model_iq": {
            "latest": point,
            "recent_days": [{**point, "date": "2026-07-12-pm", "score": 90}, point],
            "comparisons": {
                "sol_xhigh": {
                    "label": "GPT-5.6 Sol xhigh",
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "xhigh",
                    "latest": {**point, "model": "gpt-5.6-sol", "reasoning_effort": "xhigh"},
                    "recent_days": [{**point, "date": "2026-07-12-pm", "score": 120}, point],
                }
            },
            "quota_radar": {"cost_usd": 205.269295},
        },
    }


def test_parse_and_render_codex_radar_public_summary():
    report = parse_codex_radar_payload(sample_payload(), source_url="https://codexradar.com/current.json")

    assert len(report.series) == 2
    assert report.total_cost_usd == pytest.approx(205.269295)
    assert report.series[0].latest.cost_usd == pytest.approx(18.94)
    assert report.monitored_at == datetime.fromisoformat("2026-07-13T22:08:07+08:00")
    image = Image.open(BytesIO(render_codex_radar_image(report)))
    assert image.size == (1800, 1120)


class FallbackClient:
    def __init__(self):
        self.urls: list[str] = []

    async def get(self, url, headers):
        self.urls.append(url)
        if url.endswith(".jsor"):
            return httpx.Response(200, text="<html>site</html>", request=httpx.Request("GET", url))
        return httpx.Response(200, json=sample_payload(), request=httpx.Request("GET", url))


@pytest.mark.asyncio
async def test_client_falls_back_from_jsor_html_to_current_json():
    transport = FallbackClient()
    client = CodexRadarClient("https://codexradar.com/current.jsor", client=transport)

    report = await client.fetch()

    assert report.series
    assert transport.urls == [
        "https://codexradar.com/current.jsor",
        "https://codexradar.com/current.json",
    ]
