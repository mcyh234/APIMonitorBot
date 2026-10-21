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
    reason: str = ""
    http_status: int | None = None


@dataclass
class HostState:
    failures: int = 0
    retry_at: float = 0
    next_request_at: float = 0
    reason: str = ""
    http_status: int | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))
    users: int = 0


MAX_ERROR_BACKOFF = 300
HOST_REQUEST_INTERVAL = 1.0


def error_delay(failures):
    return min(MAX_ERROR_BACKOFF, 60 * 2 ** min(failures - 1, 6))


def retry_after_seconds(value):
    try:
        return max(0, int(value))
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0


class FeedPool:
    def __init__(self):
        self.states: OrderedDict[str, FeedState] = OrderedDict()
        self.hosts: OrderedDict[str, HostState] = OrderedDict()
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
        host_state = self.hosts.setdefault(host, HostState())
        self.hosts.move_to_end(host)
        # Active hosts are referenced by the bounded feed cache or an in-flight call.
        active_hosts = {urlparse(key).hostname for key in self.states}
        for key, entry in list(self.hosts.items()):
            if len(self.hosts) <= 400:
                break
            if key not in active_hosts and not entry.users and entry.retry_at <= time.monotonic():
                self.hosts.pop(key)
        if len(self.hosts) > 400:
            self.hosts.pop(host)
            return [], {"host": host, "state": "error", "post_count": 0, "latest_id": None,
                        "scope": "account", "reason": "queue_full", "retry_seconds": 60}
        now = time.monotonic()
        status = {"host": host, "state": "error", "post_count": 0, "latest_id": None}
        def cooldown():
            now = time.monotonic()
            entry, scope = (host_state, "host") if host_state.retry_at >= state.retry_at else (state, "account")
            if entry.retry_at <= now:
                return False
            status.update(state="cooldown", retry_seconds=max(1, int(entry.retry_at - now)),
                          scope=scope, reason=entry.reason)
            if entry.http_status is not None:
                status["http_status"] = entry.http_status
            return True
        if cooldown():
            return [], status
        if state.posts and now - state.fetched_at < 60:
            status.update(state="cached", post_count=len(state.posts), latest_id=state.posts[0]["id"])
            return state.posts, status
        host_state.users += 1
        try:
            async def request():
                # Space request starts and bound concurrent work per upstream.
                # Two slots let slow responses finish without starving later accounts.
                async with host_state.slots:
                    if cooldown():
                        return [], status
                    async with self.semaphore:
                        async with host_state.lock:
                            if cooldown():
                                return [], status
                            wait = host_state.next_request_at - time.monotonic()
                            if wait > 0:
                                await asyncio.sleep(wait)
                            if cooldown():
                                return [], status
                            host_state.next_request_at = time.monotonic() + HOST_REQUEST_INTERVAL
                        headers = {}
                        if state.etag:
                            headers["If-None-Match"] = state.etag
                        if state.modified:
                            headers["If-Modified-Since"] = state.modified
                        response = await asyncio.wait_for(get(url, accept="application/rss+xml, application/atom+xml, application/json", extra_headers=headers),
                                                          timeout=min(timeout, 5))
                    if response.status_code >= 400:
                        state.failures += 1
                        state.http_status = response.status_code
                        retry = retry_after_seconds(response.headers.get("Retry-After", ""))
                        state.reason = "retry_after" if retry else "http_error"
                        scope = "account"
                        if response.status_code in (429, 503):
                            # An individual account's old failures must not poison
                            # the shared host's backoff duration.
                            host_state.failures += 1
                            delay = max(error_delay(host_state.failures), retry)
                            host_state.reason, host_state.http_status = state.reason, response.status_code
                            host_state.retry_at = time.monotonic() + delay
                            scope = "host"
                            state.retry_at = host_state.retry_at
                        else:
                            delay = max(error_delay(state.failures), retry)
                            state.retry_at = time.monotonic() + delay
                        status.update(state="rate_limited" if response.status_code == 429 else "error", http_status=response.status_code,
                                      retry_seconds=int(delay), scope=scope, reason=state.reason)
                        return [], status
                    # Another already-started request may have just received a
                    # Retry-After. A concurrent success must not cancel that hold.
                    if host_state.retry_at <= time.monotonic():
                        host_state.failures, host_state.retry_at = 0, 0
                    if response.status_code == 304 and state.posts:
                        posts = state.posts
                    else:
                        posts = parse_timeline_posts(response.content, username)
                        state.etag = response.headers.get("ETag", "")[:512]
                        state.modified = response.headers.get("Last-Modified", "")[:128]
                    state.posts, state.fetched_at, state.failures, state.retry_at = posts, time.monotonic(), 0, 0
                    state.reason, state.http_status = "", None
                    status.update(state="ok", post_count=len(posts), latest_id=posts[0]["id"])
                    return posts, status
            return await asyncio.wait_for(request(), timeout=min(timeout, 18))
        except (httpx.RequestError, httpx.InvalidURL, ValueError, TimeoutError) as exc:
            state.failures += 1
            delay = error_delay(state.failures)
            state.retry_at = time.monotonic() + delay
            state.reason = "timeout" if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else "network_or_parse"
            state.http_status = None
            status.update(retry_seconds=delay, scope="account", reason=state.reason)
            return [], status
        finally:
            host_state.users -= 1
