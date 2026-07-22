from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import html
from io import BytesIO
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps


class TiboRadarError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TiboPresence:
    location_zh: str
    location_en: str
    probability: float
    confidence: str
    evidence_zh: str
    evidence_en: str
    safety_note_zh: str
    observations: int
    observed_at: datetime | None
    updated_at: datetime | None


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
    presence: TiboPresence
    post: TiboPost


class TiboRadarClient:
    def __init__(
        self,
        source_url: str = "https://codexradar.com/current.json",
        timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.source_url = source_url
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def fetch(self) -> TiboRadarReport:
        payload = await self._json(self.source_url, "Codex Radar")
        monitored_at = _datetime(payload.get("monitored_at")) or datetime.now(ZoneInfo("Asia/Shanghai"))
        timezone = str(payload.get("timezone") or "Asia/Shanghai")
        presence_raw = payload.get("tibo_presence")
        if not isinstance(presence_raw, dict):
            raise TiboRadarError("Codex Radar 公开摘要缺少 tibo_presence。")
        presence = parse_tibo_presence(presence_raw)
        source_url = _source_url(presence_raw)
        post = await self._fetch_post(source_url, presence)
        return TiboRadarReport(monitored_at=monitored_at, timezone=timezone, presence=presence, post=post)

    async def _fetch_post(self, source_url: str, presence: TiboPresence) -> TiboPost:
        username, tweet_id = _tweet_identity(source_url)
        api_url = f"https://api.fxtwitter.com/{quote(username)}/status/{quote(tweet_id)}"
        try:
            payload = await self._json(api_url, "X 帖子")
            raw = payload.get("tweet")
            if not isinstance(raw, dict):
                raise TiboRadarError("X 帖子响应缺少 tweet。")
            text = str(raw.get("text") or "").strip()
            if not text:
                raise TiboRadarError("X 帖子正文为空。")
            author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
            avatar_url = str(author.get("avatar_url") or "").strip()
            translated, translation_label = await self._translate(text, presence)
            avatar = await self._bytes(avatar_url) if avatar_url else None
            parent = await self._fetch_parent(raw, presence)
            comments = await self._extract_comments(raw, presence)
            return TiboPost(
                source_url=str(raw.get("url") or source_url),
                author_name=str(author.get("name") or "Tibo"),
                username=str(author.get("screen_name") or username),
                text=text,
                translated_zh=translated,
                translation_label=translation_label,
                created_at=_twitter_datetime(raw.get("created_at")),
                replies=_integer(raw.get("replies")),
                reposts=_integer(raw.get("retweets")),
                likes=_integer(raw.get("likes")),
                views=_integer(raw.get("views")),
                avatar=avatar,
                parent=parent,
                comments=comments,
            )
        except TiboRadarError:
            return await self._fetch_oembed(source_url, username, presence)

    async def _fetch_parent(self, raw: dict[str, Any], presence: TiboPresence) -> TiboContextPost | None:
        parent_id = str(raw.get("replying_to_status") or "").strip()
        parent_user = str(raw.get("replying_to") or "").strip()
        if not parent_id or not parent_user:
            return None
        api_url = f"https://api.fxtwitter.com/{quote(parent_user)}/status/{quote(parent_id)}"
        try:
            payload = await self._json(api_url, "被回复的原帖")
            parent_raw = payload.get("tweet")
            if not isinstance(parent_raw, dict):
                return None
            text = str(parent_raw.get("text") or "").strip()
            if not text:
                return None
            author = parent_raw.get("author") if isinstance(parent_raw.get("author"), dict) else {}
            translated, _label = await self._translate(text, presence)
            return TiboContextPost(
                source_url=str(parent_raw.get("url") or f"https://x.com/{parent_user}/status/{parent_id}"),
                author_name=str(author.get("name") or parent_user),
                username=str(author.get("screen_name") or parent_user),
                text=text,
                translated_zh=translated,
                created_at=_twitter_datetime(parent_raw.get("created_at")),
            )
        except TiboRadarError:
            return None

    async def _extract_comments(
        self,
        raw: dict[str, Any],
        presence: TiboPresence,
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
            translated, _label = await self._translate(text, presence)
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

    async def _fetch_oembed(self, source_url: str, username: str, presence: TiboPresence) -> TiboPost:
        url = f"https://publish.twitter.com/oembed?url={quote(source_url, safe='')}&omit_script=true&dnt=true"
        payload = await self._json(url, "X oEmbed")
        body = str(payload.get("html") or "")
        match = re.search(r"<p[^>]*>(.*?)</p>", body, flags=re.IGNORECASE | re.DOTALL)
        if match is None:
            raise TiboRadarError("X oEmbed 未返回帖子正文。")
        text = re.sub(r"<br\s*/?>", "\n", match.group(1), flags=re.IGNORECASE)
        text = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
        translated, translation_label = await self._translate(text, presence)
        return TiboPost(
            source_url=source_url,
            author_name=str(payload.get("author_name") or "Tibo"),
            username=username,
            text=text,
            translated_zh=translated,
            translation_label=translation_label,
            created_at=None,
            replies=None,
            reposts=None,
            likes=None,
            views=None,
            avatar=None,
        )

    async def _translate(self, text: str, presence: TiboPresence) -> tuple[str, str]:
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=auto&tl=zh-CN&dt=t&q={quote(text, safe='')}"
        )
        try:
            payload = await self._json(url, "翻译服务")
            chunks = payload[0] if isinstance(payload, list) and payload else None
            if isinstance(chunks, list):
                translated = "".join(
                    str(item[0])
                    for item in chunks
                    if isinstance(item, list) and item and isinstance(item[0], str)
                ).strip()
                if translated:
                    return translated, "中文翻译 · 机器翻译"
        except TiboRadarError:
            pass
        return presence.evidence_zh, "中文摘要 · 来自 tibo_presence"

    async def _json(self, url: str, label: str) -> Any:
        try:
            response = await self._get(url, accept="application/json")
        except httpx.RequestError as exc:
            raise TiboRadarError(f"{label}请求失败：{str(exc).strip() or exc.__class__.__name__}") from exc
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

    async def _get(self, url: str, *, accept: str) -> httpx.Response:
        headers = {"Accept": accept, "User-Agent": "APIMonitorBot/1.0"}
        if self._client is not None:
            return await self._client.get(url, headers=headers)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, verify=False, follow_redirects=True) as client:
            return await client.get(url, headers=headers)


def parse_tibo_presence(raw: dict[str, Any]) -> TiboPresence:
    return TiboPresence(
        location_zh=str(raw.get("location_label_zh") or "未知"),
        location_en=str(raw.get("location_label_en") or "Unknown"),
        probability=max(0.0, min(1.0, _number(raw.get("probability"), 0.0))),
        confidence=str(raw.get("confidence") or "unknown"),
        evidence_zh=str(raw.get("evidence_summary_zh") or "暂无中文证据摘要。"),
        evidence_en=str(raw.get("evidence_summary_en") or ""),
        safety_note_zh=str(raw.get("safety_note_zh") or "仅展示公开、粗粒度信息。"),
        observations=max(0, _integer(raw.get("observations_considered")) or 0),
        observed_at=_datetime(raw.get("observed_at")),
        updated_at=_datetime(raw.get("updated_at")),
    )


def render_tibo_radar_image(report: TiboRadarReport) -> bytes:
    width = 1600
    left, right = 84, 1516
    main_width = 950
    main_x = 120
    side_x = 1120
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
    height = max(1080, content_bottom + 150)

    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    _dashed_rectangle(draw, (left, 42, right, height - 42), fill="#94a3b8", width=2, dash=14, gap=10)
    draw.text((main_x, 76), "TIBO RADAR  /  X POST", fill="#0f172a", font=title_font)
    draw.text((main_x, 132), "最新公开帖子与粗粒度动态雷达", fill="#64748b", font=body_font)
    draw.line((main_x, 184, right - 42, 184), fill="#e2e8f0", width=2)

    avatar_x, avatar_y = main_x, 220
    _draw_avatar(image, draw, report.post.avatar, avatar_x, avatar_y, 72, body_bold)
    draw.text((avatar_x + 92, avatar_y + 4), report.post.author_name, fill="#0f172a", font=body_bold)
    verified_x = avatar_x + 92 + _text_width(report.post.author_name, body_bold) + 12
    draw.ellipse((verified_x, avatar_y + 8, verified_x + 22, avatar_y + 30), fill="#1d9bf0")
    draw.line(
        (
            verified_x + 5,
            avatar_y + 19,
            verified_x + 9,
            avatar_y + 23,
            verified_x + 17,
            avatar_y + 14,
        ),
        fill="#ffffff",
        width=2,
        joint="curve",
    )
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

    _draw_presence_panel(draw, report, side_x, 220, 350, body_font, body_bold, small_font, tiny_font)
    draw.text((main_x, height - 94), f"原帖：{report.post.source_url}", fill="#64748b", font=small_font)
    draw.text((main_x, height - 66), "数据来源：Codex Radar 公开摘要 · 仅展示公开、粗粒度信息", fill="#94a3b8", font=tiny_font)

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


def _draw_presence_panel(draw, report, x, y, width, body_font, body_bold, small_font, tiny_font) -> None:
    panel_bottom = y + 650
    draw.rounded_rectangle((x, y, x + width, panel_bottom), radius=8, fill="#f8fafc", outline="#cbd5e1", width=2)
    draw.text((x + 24, y + 24), "TIBO PRESENCE", fill="#0f172a", font=small_font)
    draw.text((x + 24, y + 60), "动态雷达", fill="#0f172a", font=body_bold)
    probability = report.presence.probability * 100
    draw.text((x + 24, y + 118), report.presence.location_zh, fill="#1d9bf0", font=body_bold)
    draw.text((x + width - 24, y + 118), f"{probability:.0f}%", fill="#0f172a", font=body_bold, anchor="ra")
    draw.text((x + 24, y + 158), report.presence.location_en, fill="#64748b", font=tiny_font)
    draw.text((x + 24, y + 190), f"置信度：{report.presence.confidence} · 公开观察 {report.presence.observations} 条", fill="#64748b", font=tiny_font)
    draw.line((x + 24, y + 224, x + width - 24, y + 224), fill="#cbd5e1", width=1)
    draw.text((x + 24, y + 248), "判断依据", fill="#475569", font=small_font)
    evidence = _wrap_paragraphs(report.presence.evidence_zh, small_font, width - 48)
    cursor = _draw_lines(draw, evidence, x + 24, y + 282, small_font, "#334155", 28)
    cursor += 20
    draw.text((x + 24, cursor), "更新时间", fill="#475569", font=small_font)
    cursor += 34
    draw.text((x + 24, cursor), _display_time(report.presence.updated_at, report.timezone) or "未知", fill="#0f172a", font=small_font)
    safety = _wrap_paragraphs(report.presence.safety_note_zh, tiny_font, width - 48)
    _draw_lines(draw, safety, x + 24, panel_bottom - 92, tiny_font, "#94a3b8", 23)


def _draw_avatar(image, draw, avatar, x, y, size, font) -> None:
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
    draw.text((x + size // 2, y + size // 2), "T", fill="#ffffff", font=font, anchor="mm")


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


def _source_url(raw: dict[str, Any]) -> str:
    direct = raw.get("source_url")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    urls = raw.get("source_urls")
    if isinstance(urls, list):
        for item in urls:
            if isinstance(item, str) and item.strip():
                return item.strip()
    raise TiboRadarError("tibo_presence 缺少 source_url/source_urls。")


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


def _twitter_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return _datetime(value)


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _text_width(text: str, font: ImageFont.ImageFont) -> int:
    box = font.getbbox(text)
    return int(box[2] - box[0])
