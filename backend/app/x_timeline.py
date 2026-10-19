"""Parse account timelines and select the newest authored public X post."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
import json
import re
from typing import Any
from xml.etree import ElementTree


class TimelineError(ValueError):
    pass


def plain_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>|</p>|</div>", "\n", value, flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def post_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            result = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def _normalize_post(raw: Any, users: dict[str, dict]) -> dict | None:
    if not isinstance(raw, dict):
        return None
    refs = raw.get("referenced_tweets")
    refs = refs if isinstance(refs, list) else []
    if raw.get("retweeted_status") or raw.get("is_retweet") or any(
        isinstance(ref, dict) and ref.get("type") == "retweeted" for ref in refs
    ):
        return None
    author = raw.get("author") or raw.get("user") or users.get(str(raw.get("author_id"))) or {}
    if not isinstance(author, dict):
        return None
    username = str(author.get("screen_name") or author.get("username") or "").lstrip("@")
    post_id = str(raw.get("id_str") or raw.get("id") or "")
    if not username or not post_id.isdigit():
        return None
    note = raw.get("note_tweet")
    text = note.get("text") if isinstance(note, dict) else None
    text = text or raw.get("full_text") or raw.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    metrics = raw.get("public_metrics") if isinstance(raw.get("public_metrics"), dict) else {}
    return {
        **raw, "id": post_id, "text": html.unescape(text.strip()),
        "url": f"https://x.com/{username}/status/{post_id}",
        "author": {"screen_name": username, "name": str(author.get("name") or username),
                   "avatar_url": author.get("avatar_url") or author.get("profile_image_url_https") or author.get("profile_image_url") or ""},
        "created_at": raw.get("created_at"),
        "replies": metrics.get("reply_count", raw.get("replies", raw.get("reply_count"))),
        "retweets": metrics.get("retweet_count", raw.get("retweets", raw.get("retweet_count"))),
        "likes": metrics.get("like_count", raw.get("likes", raw.get("favorite_count"))),
        "views": metrics.get("impression_count", raw.get("views")),
    }


def posts_from_json(payload: Any, username: str = "thsottiaux") -> list[dict]:
    includes = payload.get("includes", {}) if isinstance(payload, dict) else {}
    if not isinstance(includes, dict):
        includes = {}
    raw_users = includes.get("users")
    users = {str(user.get("id")): user for user in raw_users if isinstance(user, dict)} if isinstance(raw_users, list) else {}
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data", payload.get("tweets", payload.get("results")))
        if not isinstance(rows, list):
            props = payload.get("props")
            props = props.get("pageProps", {}) if isinstance(props, dict) else {}
            props = props if isinstance(props, dict) else {}
            timeline = props.get("timeline", payload.get("timeline", {}))
            entries = timeline.get("entries", []) if isinstance(timeline, dict) else []
            entries = entries if isinstance(entries, list) else []
            rows = [entry.get("content", {}).get("tweet") for entry in entries
                    if isinstance(entry, dict) and entry.get("type") == "tweet" and isinstance(entry.get("content"), dict)]
    else:
        rows = []
    posts = [_normalize_post(row, users) for row in rows]
    posts = [post for post in posts if post and post["author"]["screen_name"].casefold() == username.casefold()]
    if not posts:
        raise TimelineError(f"时间线中没有 @{username} 的公开帖子。")
    def sort_key(post):
        at = post_datetime(post.get("created_at"))
        # Snowflake IDs provide ordering even when a feed omits created_at.
        stamp = at.timestamp() if at else ((int(post["id"]) >> 22) + 1288834974657) / 1000
        return stamp, int(post["id"])
    for post in posts:
        refs = post.get("referenced_tweets")
        refs = refs if isinstance(refs, list) else []
        for ref in refs:
            if not isinstance(ref, dict) or ref.get("type") != "replied_to":
                continue
            parents = includes.get("tweets")
            for parent in parents if isinstance(parents, list) else []:
                if isinstance(parent, dict) and str(parent.get("id")) == str(ref.get("id")):
                    post["parent_post"] = _normalize_post(parent, users)
                    break
    unique = {post["id"]: post for post in posts}
    return sorted(unique.values(), key=sort_key, reverse=True)


def latest_from_json(payload: Any, username: str = "thsottiaux") -> dict:
    return posts_from_json(payload, username)[0]


def parse_timeline_posts(content: bytes, username: str = "thsottiaux") -> list[dict]:
    if len(content) > 2_000_000:
        raise TimelineError("X 时间线响应过大。")
    try:
        text = content.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise TimelineError("X 时间线不是有效的 UTF-8 内容。") from exc
    if text.startswith(("{", "[")):
        try:
            return posts_from_json(json.loads(text), username)
        except (ValueError, TypeError, AttributeError) as exc:
            raise TimelineError("X 时间线 JSON 无效或没有可读取的帖子。") from exc
    script = re.search(r'<script\b[^>]*\bid=[\'"]__NEXT_DATA__[\'"][^>]*>(.*?)</script>', text, re.I | re.S)
    if script:
        try:
            return posts_from_json(json.loads(script.group(1)), username)
        except (ValueError, TypeError, AttributeError) as exc:
            raise TimelineError("X 公开时间线数据无效。") from exc
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise TimelineError("X 时间线未返回可读取的 RSS/Atom 内容。")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise TimelineError("X 时间线不可读取，请配置 X API Token 或可用 RSS 来源。") from exc
    posts = []
    atom = "{http://www.w3.org/2005/Atom}"
    for item in [*root.findall("./channel/item"), *root.findall(f"{atom}entry")]:
        link = item.findtext("link") or ""
        if not link:
            link = next((entry.get("href", "") for entry in item.findall(f"{atom}link") if entry.get("rel", "alternate") == "alternate"), "")
        match = re.search(r"/([A-Za-z0-9_]+)/status/(\d+)", link)
        if not match or match.group(1).casefold() != username.casefold():
            continue
        body = item.findtext("description") or item.findtext(f"{atom}content") or item.findtext(f"{atom}summary")
        body = body or item.findtext("title") or item.findtext(f"{atom}title") or ""
        title = item.findtext("title") or item.findtext(f"{atom}title") or ""
        if title.startswith(("RT by ", "RT @")):
            continue
        posts.append({"id": match.group(2), "text": plain_html(body),
                      "created_at": item.findtext("pubDate") or item.findtext(f"{atom}published") or item.findtext(f"{atom}updated"),
                      "author": {"screen_name": match.group(1), "name": "Tibo"}})
    return posts_from_json(posts, username)


def parse_timeline(content: bytes, username: str = "thsottiaux") -> dict:
    return parse_timeline_posts(content, username)[0]
