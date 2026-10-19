from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import html
from io import BytesIO
import re
from typing import Any
from urllib.parse import quote, urlparse, urlencode
from zoneinfo import ZoneInfo

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps
from backend.app.material_theme import material_canvas, load_font as _font
from backend.app.time_utils import utc_now
from backend.app.news_accounts import account_name, normalize_username
from backend.app.x_timeline import TimelineError, posts_from_json, parse_timeline_posts, post_datetime


TIBO_USERNAME = "thsottiaux"
DEFAULT_TIMELINE_URL = f"https://fxtwitter.com/{TIBO_USERNAME}/feed.xml?count=30&with_replies=1"
FALLBACK_TIMELINE_URL = f"https://fixupx.com/{TIBO_USERNAME}/feed.xml?count=30&with_replies=1"


class TiboRadarError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TiboContextPost:
    source_url: str
    author_name: str
    username: str
    text: str
    translated_zh: str
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class TiboComment:
    author_name: str
    username: str
    text: str
    translated_zh: str
    likes: int | None = None


@dataclass(frozen=True, slots=True)
class TiboPost:
    source_url: str
    author_name: str
    username: str
    text: str
    translated_zh: str
    translation_label: str
    created_at: datetime | None
    replies: int | None
    reposts: int | None
    likes: int | None
    views: int | None
    avatar: bytes | None
    parent: TiboContextPost | None = None
    comments: tuple[TiboComment, ...] = ()


@dataclass(frozen=True, slots=True)
class TiboRadarReport:
    monitored_at: datetime
    timezone: str
    post: TiboPost


class TiboRadarClient:
    def __init__(
        self,
        source_url: str = "",
        timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
        *,
        bearer_token: str = "",
        timezone_name: str = "Asia/Shanghai",
        username: str = TIBO_USERNAME,
    ) -> None:
        # Old installations used the regional summary URL as the source.
        # Migrate that default without reading its contents or old post links.
        source_url = source_url.strip()
        if source_url.rstrip("/") == "https://codexradar.com/current.json":
            source_url = ""
        self.username = normalize_username(username)
        self._configured_source = source_url
        defaults = tuple(f"https://{host}/{self.username}/feed.xml?count=30&with_replies=1" for host in ("fxtwitter.com", "fixupx.com"))
        self.source_url = source_url.replace("{username}", self.username) if source_url else defaults[0]
        self._source_urls = (self.source_url,) if source_url else defaults
        self.timeout_seconds = timeout_seconds
        self._client = client
        self._bearer_token = bearer_token.strip()
        self.timezone = timezone_name
        self._x_user: dict | None = None

    def for_account(self, username: str) -> TiboRadarClient:
        username = normalize_username(username)
        source = self._configured_source if username == self.username else ""
        if "{username}" in self._configured_source:
            source = self._configured_source.replace("{username}", username)
        return TiboRadarClient(source, self.timeout_seconds, self._client, bearer_token=self._bearer_token,
                               timezone_name=self.timezone, username=username)

    async def fetch(self) -> TiboRadarReport:
        posts = await self.fetch_posts()
        return await self.report_for_post(posts[0])

    async def report_for_post(self, raw: dict) -> TiboRadarReport:
        post = await self._fetch_post(raw)
        return TiboRadarReport(monitored_at=utc_now(), timezone=self.timezone, post=post)

    async def fetch_posts(self) -> list[dict]:
        latest = None
        api_failed = False
        if self._bearer_token:
            try:
                latest = await self._fetch_api_timeline()
            except TiboRadarError:
                api_failed = True
        if latest is None:
            failure = None
            for source_url in self._source_urls:
                try:
                    response = await self._get(source_url, accept="application/rss+xml, application/atom+xml, application/json, text/html")
                    if response.status_code >= 400:
                        raise TiboRadarError(f"X 时间线请求失败：HTTP {response.status_code}")
                    latest = parse_timeline_posts(response.content, self.username)
                    break
                except (httpx.RequestError, TimelineError, TiboRadarError) as exc:
                    failure = exc
            if latest is None:
                prefix = "X API 与时间线来源均不可用。" if api_failed else ""
                raise TiboRadarError(prefix + "无法读取最新帖子，RSS 来源暂不可用；可通过 TIBO_RADAR_SOURCE_URL 更换来源。") from failure
        return latest

    async def _fetch_api_timeline(self) -> list[dict]:
        if self._x_user is None:
            payload = await self._json(f"https://api.x.com/2/users/by/username/{self.username}?user.fields=profile_image_url", "X 用户", authenticated=True)
            user = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(user, dict) or not str(user.get("id", "")).isdigit() or str(user.get("username", "")).casefold() != self.username:
                raise TiboRadarError("X 用户响应无效。")
            self._x_user = user
        query = urlencode({"max_results": 10, "exclude": "retweets",
                           "tweet.fields": "created_at,public_metrics,referenced_tweets,note_tweet,author_id",
                           "expansions": "author_id,referenced_tweets.id,referenced_tweets.id.author_id",
                           "user.fields": "name,username,profile_image_url"})
        payload = await self._json(f"https://api.x.com/2/users/{self._x_user['id']}/tweets?{query}", "X 最新帖子", authenticated=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise TiboRadarError("X API 未返回帖子时间线。")
        includes = payload.setdefault("includes", {})
        if not isinstance(includes, dict):
            raise TiboRadarError("X API 时间线附加信息无效。")
        if not isinstance(includes.get("users", []), list):
            raise TiboRadarError("X API 时间线用户信息无效。")
        includes.setdefault("users", []).append(self._x_user)
        try:
            latest = posts_from_json(payload, self.username)
        except (TimelineError, TypeError, AttributeError) as exc:
            raise TiboRadarError("X API 时间线中没有可读取的最新帖子。") from exc
        for post in latest:
            post["_official"] = True
        return latest

    async def _fetch_post(self, latest: dict) -> TiboPost:
        source_url = latest["url"]
        username, tweet_id = _tweet_identity(source_url)
        raw = latest
        if not latest.get("_official"):
            try:
                payload = await self._json(f"https://api.fxtwitter.com/{quote(username)}/status/{quote(tweet_id)}", "X 帖子详情")
                detail = payload.get("tweet") if isinstance(payload, dict) else None
                if isinstance(detail, dict) and _tweet_identity(str(detail.get("url") or "")) == (username, tweet_id):
                    raw = {**latest, **detail}
            except TiboRadarError:
                pass
        # A detail service may return a truncated preview; keep the full timeline
        # text whenever it is longer and never let detail lookup select a post.
        text = max((str(raw.get("text") or "").strip(), latest["text"]), key=len)
        author = raw.get("author") if isinstance(raw.get("author"), dict) else latest["author"]
        translated, translation_label = await self._translate(text)
        avatar_url = str(author.get("avatar_url") or "").strip()
        avatar = await self._bytes(avatar_url) if avatar_url else None
        parent = await self._fetch_parent(raw)
        comments = await self._extract_comments(raw)
        return TiboPost(
            source_url=source_url,
            author_name=account_name(username) if not author.get("name") or str(author["name"]).casefold() == username.casefold() else str(author["name"]),
            username=username,
            text=text,
            translated_zh=translated,
            translation_label=translation_label,
            created_at=post_datetime(raw.get("created_at")) or post_datetime(latest.get("created_at")),
            replies=_integer(raw.get("replies")),
            reposts=_integer(raw.get("retweets")),
            likes=_integer(raw.get("likes")),
            views=_integer(raw.get("views")),
            avatar=avatar,
            parent=parent,
            comments=comments,
        )

    async def _fetch_parent(self, raw: dict[str, Any]) -> TiboContextPost | None:
        parent_raw = raw.get("parent_post")
        if isinstance(parent_raw, dict) and isinstance(parent_raw.get("text"), str) and isinstance(parent_raw.get("url"), str):
            author = parent_raw.get("author") if isinstance(parent_raw.get("author"), dict) else {}
            translated, _label = await self._translate(parent_raw["text"])
            return TiboContextPost(parent_raw["url"], str(author.get("name") or "X 用户"),
                                   str(author.get("screen_name") or "unknown"), parent_raw["text"],
                                   translated, post_datetime(parent_raw.get("created_at")))
        parent_id = str(raw.get("replying_to_status") or "").strip()
        parent_user = str(raw.get("replying_to") or "").strip()
        if not parent_id or not parent_user:
            return None
        api_url = f"https://api.fxtwitter.com/{quote(parent_user)}/status/{quote(parent_id)}"
        try:
            payload = await self._json(api_url, "被回复的原帖")
            parent_raw = payload.get("tweet") if isinstance(payload, dict) else None
            if not isinstance(parent_raw, dict):
                return None
            text = str(parent_raw.get("text") or "").strip()
            if not text:
                return None
            author = parent_raw.get("author") if isinstance(parent_raw.get("author"), dict) else {}
            translated, _label = await self._translate(text)
            return TiboContextPost(
                source_url=str(parent_raw.get("url") or f"https://x.com/{parent_user}/status/{parent_id}"),
                author_name=str(author.get("name") or parent_user),
                username=str(author.get("screen_name") or parent_user),
                text=text,
                translated_zh=translated,
                created_at=post_datetime(parent_raw.get("created_at")),
            )
        except TiboRadarError:
            return None

    async def _extract_comments(
        self,
        raw: dict[str, Any],
    ) -> tuple[TiboComment, ...]:
        candidates: list[Any] = []
        for key in ("comments", "reply_tweets", "replies_data", "conversation_replies"):
            value = raw.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        comments: list[TiboComment] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not _comment_allowed(text):
                continue
            author = item.get("author") if isinstance(item.get("author"), dict) else {}
            translated, _label = await self._translate(text)
            comments.append(
                TiboComment(
                    author_name=str(author.get("name") or author.get("screen_name") or "X 用户"),
                    username=str(author.get("screen_name") or "unknown"),
                    text=text,
                    translated_zh=translated,
                    likes=_integer(item.get("likes")),
                )
            )
            if len(comments) >= 3:
                break
        return tuple(comments)

    async def _translate(self, text: str) -> tuple[str, str]:
        semaphore = asyncio.Semaphore(3)
        async def translate_chunk(chunk):
            url = ("https://translate.googleapis.com/translate_a/single"
                   f"?client=gtx&sl=auto&tl=zh-CN&dt=t&q={quote(chunk, safe='')}")
            async with semaphore:
                payload = await self._json(url, "翻译服务")
            chunks = payload[0] if isinstance(payload, list) and payload else None
            if isinstance(chunks, list):
                translated = "".join(
                    str(item[0])
                    for item in chunks
                    if isinstance(item, list) and item and isinstance(item[0], str)
                ).strip()
                if translated:
                    return translated
            raise TiboRadarError("翻译服务未返回中文译文。")
        chunks = _translation_chunks(text, 1800)
        results = await asyncio.gather(*(translate_chunk(chunk) for chunk in chunks), return_exceptions=True)
        if results and all(isinstance(result, str) for result in results):
            return "\n".join(results), "中文翻译 · 机器翻译"
        async def translate_fallback(chunk):
            query = urlencode({"q": chunk, "langpair": "en|zh-CN"})
            async with semaphore:
                payload = await self._json(f"https://api.mymemory.translated.net/get?{query}", "备用翻译服务")
            if not isinstance(payload, dict) or payload.get("responseStatus") != 200 or payload.get("quotaFinished"):
                raise TiboRadarError("备用翻译服务不可用。")
            body = payload.get("responseData")
            translated = body.get("translatedText") if isinstance(body, dict) else None
            if not isinstance(translated, str) or not translated.strip():
                raise TiboRadarError("备用翻译服务未返回译文。")
            return html.unescape(translated.strip())
        chunks = _translation_chunks(text, 480)
        results = await asyncio.gather(*(translate_fallback(chunk) for chunk in chunks), return_exceptions=True)
        if results and all(isinstance(result, str) for result in results):
            return "\n".join(results), "中文翻译 · 机器翻译"
        return "翻译暂不可用，请查看上方原文。", "中文翻译 · 暂不可用"

    async def _json(self, url: str, label: str, *, authenticated: bool = False) -> Any:
        try:
            response = await self._get(url, accept="application/json", authenticated=authenticated)
        except httpx.RequestError as exc:
            raise TiboRadarError(f"{label}请求失败：{exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            raise TiboRadarError(f"{label}请求失败：HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise TiboRadarError(f"{label}响应不是 JSON。") from exc

    async def _bytes(self, url: str) -> bytes | None:
        try:
            response = await self._get(url, accept="image/*")
            return response.content if response.status_code < 400 and response.content else None
        except httpx.RequestError:
            return None

    async def _get(self, url: str, *, accept: str, authenticated: bool = False) -> httpx.Response:
        headers = {"Accept": accept, "User-Agent": "APIMonitorBot/1.0"}
        if authenticated:
            if urlparse(url).netloc != "api.x.com" or urlparse(url).scheme != "https":
                raise TiboRadarError("X API Token 只能发送至官方 API。")
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        if self._client is not None:
            if authenticated:
                return await self._client.get(url, headers=headers, follow_redirects=False)
            return await self._client.get(url, headers=headers)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, verify=False, follow_redirects=not authenticated) as client:
            return await client.get(url, headers=headers)


def _translation_chunks(text: str, maximum_bytes: int) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        size, end = 0, start
        while end < len(text) and size + len(text[end].encode("utf-8")) <= maximum_bytes:
            size += len(text[end].encode("utf-8"))
            end += 1
        if end < len(text):
            boundary = max(text.rfind(" ", start + (end - start) // 2, end),
                           text.rfind("\n", start + (end - start) // 2, end))
            if boundary >= start:
                end = boundary + 1
        chunks.append(text[start:end])
        start = end
    return chunks


def render_tibo_radar_image(report: TiboRadarReport, *, generated_at: datetime | None = None) -> bytes:
    width = 1600
    left, right = 84, 1516
    main_x = 120
    main_width = right - main_x - 42
    body_font = _font(25)
    body_bold = _font(25, bold=True)
    small_font = _font(17)
    tiny_font = _font(14)
    context_font = _font(20)
    context_bold = _font(20, bold=True)
    title_font = _font(42, bold=True)
    original_lines = _wrap_paragraphs(report.post.text, body_font, main_width - 54)
    translated_lines = _wrap_paragraphs(report.post.translated_zh, body_font, main_width - 54)
    parent_original_lines: list[str] = []
    parent_translated_lines: list[str] = []
    if report.post.parent is not None:
        parent_original_lines = _wrap_paragraphs(report.post.parent.text, context_font, main_width - 82)
        parent_translated_lines = _wrap_paragraphs(report.post.parent.translated_zh, context_font, main_width - 82)
    comment_lines: list[tuple[TiboComment, list[str], list[str]]] = []
    for comment in report.post.comments:
        original = _wrap_paragraphs(comment.text, small_font, main_width - 94)
        translated = _wrap_paragraphs(comment.translated_zh, small_font, main_width - 94)
        comment_lines.append((comment, original, translated))
    metrics = _post_metrics(report.post)

    content_bottom = 324 + len(original_lines) * 38
    if metrics:
        content_bottom += 46
    content_bottom += 96 + len(translated_lines) * 38
    if report.post.parent is not None:
        content_bottom += 34 + _parent_block_height(parent_original_lines, parent_translated_lines)
    if comment_lines:
        content_bottom += 34 + _comments_block_height(comment_lines)
    height = max(800, content_bottom + 150)

    image, draw = material_canvas((width, height), generated_at)
    _dashed_rectangle(draw, (left, 42, right, height - 42), fill="#94a3b8", width=2, dash=14, gap=10)
    title = "TIBO RADAR" if report.post.username.casefold() == TIBO_USERNAME else "AI NEWS"
    draw.text((main_x, 76), f"{title}  /  X POST", fill="#0f172a", font=title_font)
    draw.text((main_x, 132), f"@{report.post.username} 公开帖子 · 原文与中文翻译", fill="#64748b", font=body_font)
    draw.line((main_x, 184, right - 42, 184), fill="#e2e8f0", width=2)

    avatar_x, avatar_y = main_x, 220
    _draw_avatar(image, draw, report.post.avatar, avatar_x, avatar_y, 72, body_bold, report.post.author_name[:1])
    draw.text((avatar_x + 92, avatar_y + 4), report.post.author_name, fill="#0f172a", font=body_bold)
    draw.text((avatar_x + 92, avatar_y + 40), f"@{report.post.username}", fill="#536471", font=small_font)
    date_text = _display_time(report.post.created_at, report.timezone)
    if date_text:
        draw.text((main_x + main_width - 26, avatar_y + 12), date_text, fill="#536471", font=small_font, anchor="ra")

    body_y = avatar_y + 104
    body_y = _draw_lines(draw, original_lines, main_x + 4, body_y, body_font, "#0f172a", 38)
    if metrics:
        draw.text((main_x + 4, body_y + 12), metrics, fill="#536471", font=small_font)
        body_y += 46
    draw.line((main_x + 4, body_y + 18, main_x + main_width - 20, body_y + 18), fill="#e2e8f0", width=2)
    body_y += 48
    draw.text((main_x + 4, body_y), report.post.translation_label, fill="#1d9bf0", font=body_bold)
    body_y += 48
    body_y = _draw_lines(draw, translated_lines, main_x + 4, body_y, body_font, "#1e293b", 38)

    if report.post.parent is not None:
        body_y += 34
        body_y = _draw_parent_post(
            draw,
            report.post.parent,
            parent_original_lines,
            parent_translated_lines,
            main_x + 4,
            body_y,
            main_width - 24,
            report.timezone,
            context_font,
            context_bold,
            small_font,
        )
    if comment_lines:
        body_y += 34
        _draw_comments(
            draw,
            comment_lines,
            main_x + 4,
            body_y,
            main_width - 24,
            context_bold,
            small_font,
            tiny_font,
        )

    draw.text((main_x, height - 94), f"原帖：{report.post.source_url}", fill="#64748b", font=small_font)
    draw.text((main_x, height - 66), f"数据来源：X / @{report.post.username} · 抓取时间：{_display_time(report.monitored_at, report.timezone)}", fill="#94a3b8", font=tiny_font)

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _draw_parent_post(
    draw,
    parent: TiboContextPost,
    original_lines: list[str],
    translated_lines: list[str],
    x: int,
    y: int,
    width: int,
    timezone: str,
    font,
    bold_font,
    small_font,
) -> int:
    block_height = _parent_block_height(original_lines, translated_lines)
    draw.rounded_rectangle((x, y, x + width, y + block_height), radius=8, fill="#f8fafc", outline="#cbd5e1", width=2)
    draw.text((x + 22, y + 18), "回复的原帖", fill="#64748b", font=small_font)
    draw.text((x + 22, y + 50), parent.author_name, fill="#0f172a", font=bold_font)
    author_width = _text_width(parent.author_name, bold_font)
    draw.text((x + 34 + author_width, y + 53), f"@{parent.username}", fill="#536471", font=small_font)
    date_text = _display_time(parent.created_at, timezone)
    if date_text:
        draw.text((x + width - 22, y + 53), date_text, fill="#64748b", font=small_font, anchor="ra")
    cursor = _draw_lines(draw, original_lines, x + 22, y + 88, font, "#0f172a", 31)
    draw.line((x + 22, cursor + 5, x + width - 22, cursor + 5), fill="#e2e8f0", width=1)
    cursor += 20
    draw.text((x + 22, cursor), "中文翻译", fill="#1d9bf0", font=small_font)
    cursor += 30
    _draw_lines(draw, translated_lines, x + 22, cursor, font, "#334155", 31)
    return y + block_height


def _draw_comments(draw, comments, x, y, width, heading_font, font, tiny_font) -> int:
    draw.text((x, y), "部分公开评论", fill="#0f172a", font=heading_font)
    cursor = y + 42
    for comment, original_lines, translated_lines in comments:
        block_height = _comment_block_height(original_lines, translated_lines)
        draw.rounded_rectangle((x, cursor, x + width, cursor + block_height), radius=7, fill="#ffffff", outline="#e2e8f0", width=1)
        draw.text((x + 18, cursor + 14), f"{comment.author_name}  @{comment.username}", fill="#0f172a", font=font)
        if comment.likes is not None:
            draw.text((x + width - 18, cursor + 15), f"喜欢 {comment.likes}", fill="#64748b", font=tiny_font, anchor="ra")
        line_y = _draw_lines(draw, original_lines, x + 18, cursor + 44, font, "#334155", 27)
        _draw_lines(draw, translated_lines, x + 18, line_y + 4, font, "#64748b", 27)
        cursor += block_height + 12
    return cursor


def _parent_block_height(original_lines: list[str], translated_lines: list[str]) -> int:
    return 148 + (len(original_lines) + len(translated_lines)) * 31


def _comment_block_height(original_lines: list[str], translated_lines: list[str]) -> int:
    return 100 + (len(original_lines) + len(translated_lines)) * 27


def _comments_block_height(comments: list[tuple[TiboComment, list[str], list[str]]]) -> int:
    return 42 + sum(
        _comment_block_height(original, translated) + 12
        for _comment, original, translated in comments
    )


def _draw_avatar(image, draw, avatar, x, y, size, font, initial="T") -> None:
    if avatar:
        try:
            source = Image.open(BytesIO(avatar)).convert("RGB")
            source = ImageOps.fit(source, (size, size), method=Image.Resampling.LANCZOS)
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
            image.paste(source, (x, y), mask)
            return
        except Exception:
            pass
    draw.ellipse((x, y, x + size, y + size), fill="#0f172a")
    draw.text((x + size // 2, y + size // 2), initial, fill=draw.theme.on_primary_container, font=font, anchor="mm")


_SENSITIVE_COMMENT_TERMS = (
    "nsfw",
    "porn",
    "nude",
    "sex",
    "suicide",
    "kill yourself",
    "terror",
    "裸照",
    "色情",
    "约炮",
    "自杀",
    "恐怖袭击",
    "身份证",
    "手机号",
)


def _comment_allowed(text: str) -> bool:
    clean = text.strip()
    if len(clean) < 2 or len(clean) > 280:
        return False
    folded = clean.casefold()
    if "http://" in folded or "https://" in folded or "www." in folded:
        return False
    return not any(term in folded for term in _SENSITIVE_COMMENT_TERMS)


def _tweet_identity(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    match = re.search(r"/([^/]+)/status/(\d+)", parsed.path)
    if match is None:
        raise TiboRadarError("无法从 source_url 解析 X 帖子 ID。")
    return match.group(1), match.group(2)


def _wrap_paragraphs(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    result: list[str] = []
    for paragraph in text.splitlines() or [""]:
        if not paragraph.strip():
            result.append("")
            continue
        current = ""
        for char in paragraph:
            candidate = current + char
            if current and _text_width(candidate, font) > max_width:
                result.append(current.rstrip())
                current = char.lstrip()
            else:
                current = candidate
        if current:
            result.append(current.rstrip())
    return result


def _draw_lines(draw, lines, x, y, font, fill, line_height) -> int:
    cursor = y
    for line in lines:
        if line:
            draw.text((x, cursor), line, fill=fill, font=font)
        cursor += line_height
    return cursor


def _dashed_rectangle(draw, box, *, fill, width, dash, gap) -> None:
    left, top, right, bottom = box
    for start in range(left, right, dash + gap):
        draw.line((start, top, min(start + dash, right), top), fill=fill, width=width)
        draw.line((start, bottom, min(start + dash, right), bottom), fill=fill, width=width)
    for start in range(top, bottom, dash + gap):
        draw.line((left, start, left, min(start + dash, bottom)), fill=fill, width=width)
        draw.line((right, start, right, min(start + dash, bottom)), fill=fill, width=width)


def _post_metrics(post: TiboPost) -> str:
    parts: list[str] = []
    for label, value in (("回复", post.replies), ("转帖", post.reposts), ("喜欢", post.likes), ("浏览", post.views)):
        if value is not None:
            parts.append(f"{label} {_compact_number(value)}")
    return "   ·   ".join(parts)


def _compact_number(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _display_time(value: datetime | None, timezone: str) -> str:
    if value is None:
        return ""
    try:
        return value.astimezone(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value.isoformat(timespec="minutes")


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text_width(text: str, font: ImageFont.ImageFont) -> int:
    box = font.getbbox(text)
    return int(box[2] - box[0])
