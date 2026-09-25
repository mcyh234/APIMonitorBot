"""Render offline light/dark report samples without contacting API providers."""
from __future__ import annotations

import argparse
import asyncio
import runpy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.check_image import CheckResultImageRow, render_check_result_image
from backend.app.codex_radar import parse_codex_radar_payload, render_codex_radar_image
from backend.app.model_pricing import ModelTokenPrice
from backend.app.status_bars import ConfigStatusBarsData, StatusBucketData, StatusWindowData
from backend.app.status_image import render_status_image
from backend.app.sub2_price_image import Sub2PriceBoard, render_sub2_price_image
from backend.app.sub2_rates import Sub2RateHistoryPoint, Sub2StoredRate
from backend.app.sub2_sentiment import Sub2SentimentSummary, sentiment_date
from backend.app.tibo_radar import TiboRadarClient, render_tibo_radar_image


async def render(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    radar_fixture = runpy.run_path(str(ROOT / "tests" / "test_codex_radar.py"))
    tibo_fixture = runpy.run_path(str(ROOT / "tests" / "test_tibo_radar.py"))
    radar = parse_codex_radar_payload(radar_fixture["sample_payload"](), source_url="https://codexradar.com/current.json")
    tibo = await TiboRadarClient(client=tibo_fixture["TiboFixtureClient"]()).fetch()
    for mode, hour in [("light", 4), ("dark", 14)]:
        now = datetime(2026, 9, 25, hour, tzinfo=timezone.utc)
        windows = []
        for name, minutes, count in [("30m", 1, 30), ("5h", 10, 30), ("24h", 60, 24)]:
            start = now - timedelta(minutes=minutes * count)
            buckets = [StatusBucketData(start + timedelta(minutes=minutes * i), start + timedelta(minutes=minutes * (i + 1)),
                       "down" if i in (10, 11) else "ok", 0 if i in (10, 11) else 1, 1 if i in (10, 11) else 0, 1, i in (3, 4), 1 if i in (3, 4) else 0) for i in range(count)]
            points = [{"at": b.start_at, "latency_ms": 380 + (i % 5) * 75 if i != 18 else 2300,
                       "model_switched": i == 12, "code": "200"} for i, b in enumerate(buckets)]
            windows.append(StatusWindowData(name, {"30m": "最近30分钟", "5h": "最近5小时", "24h": "最近24小时"}[name], minutes, buckets, points))
        config = ConfigStatusBarsData(1, "API Monitor · 主线路", "G123456", "claude-sonnet / auto", "ok", "200", 98.6, windows)
        (output / f"status-{mode}.png").write_bytes(render_status_image([config], timezone_name="Asia/Shanghai", generated_at=now))
        (output / f"check-{mode}.png").write_bytes(render_check_result_image([
            CheckResultImageRow("主线路 / Claude", True, "200", 99.4, 420),
            CheckResultImageRow("备用线路 / OpenAI", False, "EMPTY_ASSISTANT_CONTENT", 94.1, 2200),
        ], timezone_name="Asia/Shanghai", generated_at=now))
        rates = []
        for platform, name, multiplier in [("openai", "Plus 标准分组", .15), ("anthropic", "Claude 优选分组", .25)]:
            history = tuple(Sub2RateHistoryPoint(now - timedelta(days=5 - i), multiplier + (i % 3) * .02) for i in range(6))
            rates.append(Sub2StoredRate(platform, name, name, multiplier, now, history))
        prices = tuple(ModelTokenPrice(model, platform, .0000025, .000015, 0, .00000025)
                       for model, platform in [("gpt-5.4", "openai"), ("claude-sonnet", "anthropic")])
        (output / f"price-{mode}.png").write_bytes(render_sub2_price_image([Sub2PriceBoard("API 价格监控", rates, model_prices=prices)],
              sentiment=Sub2SentimentSummary(sentiment_date(now), 3, 2), generated_at=now))
        (output / f"radar-{mode}.png").write_bytes(render_codex_radar_image(radar, generated_at=now))
        (output / f"tibo-{mode}.png").write_bytes(render_tibo_radar_image(tibo, generated_at=now))
    print(output.resolve())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "theme-preview")
    asyncio.run(render(parser.parse_args().output))
