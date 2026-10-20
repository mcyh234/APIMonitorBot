"""Read-only RSS diagnostic: never translates, reviews, or sends a notification."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.settings import Settings
from backend.app.news_accounts import normalize_username
from backend.app.tibo_radar import TiboRadarClient, TiboRadarError


async def check(args):
    settings = Settings()
    username = normalize_username(args.username)
    single = settings.tibo_radar_source_url
    client = TiboRadarClient(username=username, source_url=single if not args.source and (username == "thsottiaux" or "{username}" in single) else "",
                            source_urls=args.source or [url for url in settings.tibo_radar_source_urls if "{username}" in url or username == "thsottiaux"])
    try:
        posts = await client.fetch_posts()
    except TiboRadarError:
        posts = []
    result = {"username": client.username, "sources": client.source_status,
              "posts": [{"id": post["id"], "created_at": post.get("created_at"), "url": post["url"]} for post in posts]}
    if args.post_id:
        result["requested_post_present"] = any(post["id"] == args.post_id for post in posts)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if posts else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="thsottiaux")
    parser.add_argument("--source", action="append", help="RSS URL/template; repeat for multiple sources")
    parser.add_argument("--post-id", help="Check whether a missed X post ID is present")
    sys.exit(asyncio.run(check(parser.parse_args())))
