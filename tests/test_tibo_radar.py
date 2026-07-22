from io import BytesIO
from dataclasses import replace

import httpx
import pytest
from PIL import Image

from backend.app.tibo_radar import TiboRadarClient, render_tibo_radar_image


class TiboFixtureClient:
    async def get(self, url, headers):
        request = httpx.Request("GET", url)
        if "current.json" in url:
            return httpx.Response(
                200,
                json={
                    "monitored_at": "2026-07-13T22:08:07+08:00",
                    "timezone": "Asia/Shanghai",
                    "tibo_presence": {
                        "location_label_zh": "旧金山湾区 / PT",
                        "location_label_en": "San Francisco Bay Area / PT",
                        "probability": 0.3,
                        "confidence": "low",
                        "evidence_summary_zh": "近期公开帖与太平洋时区大致相符。",
                        "evidence_summary_en": "Recent posts roughly align with PT.",
                        "source_urls": ["https://x.com/thsottiaux/status/123456"],
                        "safety_note_zh": "仅展示公开粗粒度信息。",
                        "observations_considered": 40,
                        "updated_at": "2026-07-13T14:08:27Z",
                    },
                },
                request=request,
            )
        if "api.fxtwitter.com" in url:
            if url.endswith("/654321"):
                return httpx.Response(
                    200,
                    json={
                        "tweet": {
                            "url": "https://x.com/maria_rcks/status/654321",
                            "text": "What do you think about this update?",
                            "created_at": "Sun Jul 12 16:00:00 +0000 2026",
                            "author": {"name": "Maria", "screen_name": "maria_rcks"},
                        }
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "tweet": {
                        "url": "https://x.com/thsottiaux/status/123456",
                        "text": "Morning. Three important updates.",
                        "created_at": "Sun Jul 12 17:59:57 +0000 2026",
                        "replies": 10,
                        "retweets": 20,
                        "likes": 30,
                        "views": 400,
                        "replying_to": "maria_rcks",
                        "replying_to_status": "654321",
                        "comments": [
                            {
                                "text": "This is useful context.",
                                "likes": 12,
                                "author": {"name": "Safe User", "screen_name": "safe_user"},
                            },
                            {
                                "text": "NSFW spam comment",
                                "likes": 50,
                                "author": {"name": "Spam", "screen_name": "spam"},
                            },
                            {
                                "text": "See https://spam.example",
                                "likes": 20,
                                "author": {"name": "Link", "screen_name": "link"},
                            },
                        ],
                        "author": {"name": "Tibo", "screen_name": "thsottiaux"},
                    }
                },
                request=request,
            )
        if "translate.googleapis.com" in url:
            return httpx.Response(200, json=[[['早上好。三个重要更新。', 'Morning. Three important updates.']]], request=request)
        raise AssertionError(url)


@pytest.mark.asyncio
async def test_tibo_client_fetches_post_translates_and_renders():
    report = await TiboRadarClient(client=TiboFixtureClient()).fetch()

    assert report.post.username == "thsottiaux"
    assert report.post.translated_zh == "早上好。三个重要更新。"
    assert report.post.parent is not None
    assert report.post.parent.username == "maria_rcks"
    assert [item.username for item in report.post.comments] == ["safe_user"]
    assert report.presence.probability == pytest.approx(0.3)
    image = Image.open(BytesIO(render_tibo_radar_image(report)))
    assert image.width == 1600
    assert image.height >= 1080

    assert report.post.parent is not None
    long_parent = replace(
        report.post.parent,
        text="Long original post content. " * 80,
        translated_zh="很长的原帖中文翻译内容。" * 80,
    )
    long_report = replace(report, post=replace(report.post, parent=long_parent))
    long_image = Image.open(BytesIO(render_tibo_radar_image(long_report)))
    assert long_image.height > 1080
