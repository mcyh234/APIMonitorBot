from io import BytesIO
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from PIL import Image

from backend.app.tibo_radar import TiboRadarClient, render_tibo_radar_image


class TiboFixtureClient:
    async def get(self, url, headers):
        request = httpx.Request("GET", url)
        if "/feed.xml" in url or "/with_replies/rss" in url:
            return httpx.Response(
                200,
                json={
                    "tweets": [
                        {"id": "120000", "text": "Older pinned post.", "created_at": "2026-07-11T10:00:00Z",
                         "author": {"name": "Tibo", "screen_name": "thsottiaux"}},
                        {"id": "123456", "text": "Morning. Three important updates.", "created_at": "2026-07-12T17:59:57Z",
                         "author": {"name": "Tibo", "screen_name": "thsottiaux"}},
                    ],
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
            text = parse_qs(urlparse(url).query)["q"][0]
            translations = {
                "Morning. Three important updates.": "早上好。三个重要更新。",
                "What do you think about this update?": "你如何看待这次更新？",
                "This is useful context.": "这段背景信息很有用。",
            }
            return httpx.Response(200, json=[[[translations[text], text]]], request=request)
        raise AssertionError(url)


@pytest.mark.asyncio
async def test_tibo_client_fetches_post_translates_and_renders():
    report = await TiboRadarClient(client=TiboFixtureClient()).fetch()

    assert report.post.username == "thsottiaux"
    assert report.post.translated_zh == "早上好。三个重要更新。"
    assert report.post.parent is not None
    assert report.post.parent.username == "maria_rcks"
    assert [item.username for item in report.post.comments] == ["safe_user"]
    assert not hasattr(report, "presence")
    image = Image.open(BytesIO(render_tibo_radar_image(report)))
    assert image.width == 1600
    assert image.height >= 800

    assert report.post.parent is not None
    long_parent = replace(
        report.post.parent,
        text="Long original post content. " * 80,
        translated_zh="很长的原帖中文翻译内容。" * 80,
    )
    long_report = replace(report, post=replace(report.post, parent=long_parent))
    long_image = Image.open(BytesIO(render_tibo_radar_image(long_report)))
    assert long_image.height > image.height
