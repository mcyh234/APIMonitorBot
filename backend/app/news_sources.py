"""Bounded public feed pool; health metadata never contains response text or URLs."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time
from urllib.parse import urlparse

import httpx

from backend.app.x_timeline import parse_timeline_posts


DEFAULT_FEED_TEMPLATES = (
    "https://fxtwitter.com/{username}/feed.xml?count=100&with_replies=1",
    "https://nitter.kareem.one/{username}/with_replies/rss",
    "https://nitter.jaydenha.uk/{username}/with_replies/rss",
    "https://nitter.netbub.com/{username}/with_replies/rss",
)


@dataclass
class FeedState:
    failures: int = 0
    retry_at: float = 0
    fetched_at: float = 0
    posts: list[dict] = field(default_factory=list)
    etag: str = ""
    modified: str = ""


class FeedPool:
    def __init__(self):
        self.states: OrderedDict[str, FeedState] = OrderedDict()
        self.host_limits: dict[str, float] = {}
        self.semaphore = asyncio.Semaphore(8)

    async def fetch(self, url, username, get, timeout):
        try:
            parsed = urlparse(url)
            host = parsed.hostname
            if not host or parsed.scheme not in ("https", "http"):
                raise ValueError("Invalid RSS URL")
        except ValueError:
            return [], {"host": "RSS 配置无效", "state": "error", "post_count": 0, "latest_id": None}
        state = self.states.setdefault(url, FeedState())
        self.states.move_to_end(url)
        while len(self.states) > 400:
            self.states.popitem(last=False)
        now = time.monotonic()
        status = {"host": host, "state": "error", "post_count": 0, "latest_id": None}
        retry_at = max(state.retry_at, self.host_limits.get(host, 0))
        if retry_at > now:
            status.update(state="cooldown", retry_seconds=max(1, int(retry_at - now)))
            return [], status
        if state.posts and now - state.fetched_at < 60:
            status.update(state="cached", post_count=len(state.posts), latest_id=state.posts[0]["id"])
            return state.posts, status
        try:
            async def request():
                async with self.semaphore:
                    # A simultaneous account may already have received a 429.
                    if self.host_limits.get(host, 0) > time.monotonic():
                        return None
                    headers = {}
                    if state.etag:
                        headers["If-None-Match"] = state.etag
                    if state.modified:
                        headers["If-Modified-Since"] = state.modified
                    return await asyncio.wait_for(get(url, accept="application/rss+xml, application/atom+xml, application/json", extra_headers=headers),
                                                  timeout=min(timeout, 5))
            response = await asyncio.wait_for(request(), timeout=min(timeout, 12))
            if response is None:
                status.update(state="cooldown", retry_seconds=max(1, int(self.host_limits[host] - time.monotonic())))
                return [], status
            if response.status_code == 304 and state.posts:
                posts = state.posts
            else:
                if response.status_code >= 400:
                    state.failures += 1
                    delay = min(3600, 60 * 2 ** min(state.failures - 1, 6))
                    retry = response.headers.get("Retry-After", "")
                    try:
                        delay = max(delay, int(retry))
                    except ValueError:
                        try:
                            delay = max(delay, (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError, OverflowError):
                            pass
                    state.retry_at = time.monotonic() + delay
                    if response.status_code in (429, 503):
                        self.host_limits[host] = max(self.host_limits.get(host, 0), state.retry_at)
                    status.update(state="rate_limited" if response.status_code == 429 else "error", http_status=response.status_code, retry_seconds=int(delay))
                    return [], status
                posts = parse_timeline_posts(response.content, username)
                state.etag = response.headers.get("ETag", "")[:512]
                state.modified = response.headers.get("Last-Modified", "")[:128]
            state.posts, state.fetched_at, state.failures, state.retry_at = posts, time.monotonic(), 0, 0
            status.update(state="ok", post_count=len(posts), latest_id=posts[0]["id"])
            return posts, status
        except (httpx.RequestError, httpx.InvalidURL, ValueError, TimeoutError):
            state.failures += 1
            delay = min(3600, 60 * 2 ** min(state.failures - 1, 6))
            state.retry_at = time.monotonic() + delay
            status.update(retry_seconds=delay)
            return [], status
