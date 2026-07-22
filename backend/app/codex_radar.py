from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
import math
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
from PIL import Image, ImageDraw, ImageFont


class CodexRadarError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RadarPoint:
    batch: str
    score: float
    passed: int
    tasks: int
    cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class RadarSeries:
    key: str
    label: str
    model: str
    reasoning_effort: str
    points: tuple[RadarPoint, ...]

    @property
    def latest(self) -> RadarPoint:
        return self.points[-1]


@dataclass(frozen=True, slots=True)
class CodexRadarReport:
    monitored_at: datetime
    timezone: str
    series: tuple[RadarSeries, ...]
    total_cost_usd: float | None
    source_url: str


class CodexRadarClient:
    def __init__(
        self,
        source_url: str,
        timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.source_url = source_url
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def fetch(self) -> CodexRadarReport:
        errors: list[str] = []
        for url in _candidate_urls(self.source_url):
            try:
                response = await self._get(url)
                if response.status_code >= 400:
                    raise CodexRadarError(f"HTTP {response.status_code}")
                payload = response.json()
                return parse_codex_radar_payload(payload, source_url=url)
            except (httpx.RequestError, ValueError, CodexRadarError) as exc:
                errors.append(f"{url}: {str(exc).strip() or exc.__class__.__name__}")
        raise CodexRadarError("读取 Codex Radar 数据失败：" + "；".join(errors))

    async def _get(self, url: str) -> httpx.Response:
        headers = {"Accept": "application/json", "User-Agent": "APIMonitorBot/1.0"}
        if self._client is not None:
            return await self._client.get(url, headers=headers)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, verify=False, follow_redirects=True) as client:
            return await client.get(url, headers=headers)


def _candidate_urls(source_url: str) -> tuple[str, ...]:
    clean = source_url.strip()
    if not clean:
        raise CodexRadarError("Codex Radar 数据地址不能为空。")
    parsed = urlparse(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CodexRadarError("Codex Radar 数据地址必须是 HTTP(S) URL。")
    fallback = urljoin(clean, "/current.json")
    return (clean,) if clean == fallback else (clean, fallback)


def parse_codex_radar_payload(payload: Any, *, source_url: str) -> CodexRadarReport:
    if not isinstance(payload, dict):
        raise CodexRadarError("Codex Radar 响应不是 JSON 对象。")
    model_iq = payload.get("model_iq")
    if not isinstance(model_iq, dict):
        raise CodexRadarError("Codex Radar 响应缺少 model_iq。")

    series: list[RadarSeries] = []
    primary_latest = model_iq.get("latest")
    primary_days = model_iq.get("recent_days")
    if isinstance(primary_latest, dict) and isinstance(primary_days, list):
        primary = _parse_series("primary", primary_latest, primary_days, None)
        if primary is not None:
            series.append(primary)

    comparisons = model_iq.get("comparisons")
    if isinstance(comparisons, dict):
        for key, raw in comparisons.items():
            if not isinstance(raw, dict):
                continue
            latest = raw.get("latest")
            recent_days = raw.get("recent_days")
            if not isinstance(latest, dict) or not isinstance(recent_days, list):
                continue
            parsed = _parse_series(str(key), latest, recent_days, raw)
            if parsed is not None:
                series.append(parsed)
    if not series:
        raise CodexRadarError("Codex Radar 没有可绘制的模型 IQ 配置。")

    monitored_at = _parse_datetime(payload.get("monitored_at"))
    timezone = str(payload.get("timezone") or "Asia/Shanghai")
    quota_radar = model_iq.get("quota_radar")
    total_cost = _optional_float(quota_radar.get("cost_usd")) if isinstance(quota_radar, dict) else None
    return CodexRadarReport(
        monitored_at=monitored_at,
        timezone=timezone,
        series=tuple(series),
        total_cost_usd=total_cost,
        source_url=source_url,
    )


def _parse_series(
    key: str,
    latest: dict[str, Any],
    recent_days: list[Any],
    metadata: dict[str, Any] | None,
) -> RadarSeries | None:
    model = str((metadata or {}).get("model") or latest.get("model") or "unknown")
    effort = str((metadata or {}).get("reasoning_effort") or latest.get("reasoning_effort") or "max")
    label = str((metadata or {}).get("label") or _series_label(model, effort))
    points = [_parse_point(item) for item in recent_days if isinstance(item, dict)]
    points = [item for item in points if item is not None]
    latest_point = _parse_point(latest)
    if latest_point is not None:
        points = [item for item in points if item.batch != latest_point.batch]
        points.append(latest_point)
    points.sort(key=lambda item: _batch_sort_key(item.batch))
    if not points:
        return None
    return RadarSeries(key=key, label=label, model=model, reasoning_effort=effort, points=tuple(points))


def _parse_point(raw: dict[str, Any]) -> RadarPoint | None:
    batch = str(raw.get("date") or "").strip()
    score = _optional_float(raw.get("score"))
    if not batch or score is None:
        return None
    return RadarPoint(
        batch=batch,
        score=score,
        passed=_safe_int(raw.get("passed")),
        tasks=_safe_int(raw.get("tasks") or raw.get("valid_tasks")),
        cost_usd=_optional_float(raw.get("cost_usd")),
    )


def render_codex_radar_image(report: CodexRadarReport) -> bytes:
    width, height = 1800, 1120
    image = Image.new("RGB", (width, height), "#f3f6f9")
    draw = ImageDraw.Draw(image)
    title_font = _font(56, bold=True)
    heading_font = _font(28, bold=True)
    body_font = _font(18)
    body_bold = _font(18, bold=True)
    small_font = _font(15)
    score_font = _font(30, bold=True)

    draw.text((80, 66), "CODEX RADAR  /  STATIC REPORT", fill="#dc4338", font=heading_font)
    draw.text((80, 112), "近期降智雷达", fill="#17202b", font=title_font)
    batch_keys = _visible_batches(report.series)
    draw.text((82, 188), f"多配置 IQ 指数 · 最近 {len(batch_keys)} 个测试批次", fill="#667785", font=heading_font)
    local_time = report.monitored_at.astimezone(ZoneInfo(report.timezone))
    draw.text((1570, 76), "数据快照", fill="#7a8792", font=small_font, anchor="ra")
    draw.text((1720, 112), local_time.strftime("%Y-%m-%d %H:%M"), fill="#17202b", font=heading_font, anchor="ra")
    draw.text((1720, 154), "公开摘要 · 静态图", fill="#7a8792", font=body_font, anchor="ra")
    draw.line((80, 232, 1720, 232), fill="#c9d1d8", width=2)

    plot = (110, 310, 1120, 900)
    _draw_plot(draw, report.series, batch_keys, plot, body_font, small_font, body_bold)
    _draw_ranking(
        draw,
        report.series,
        (1350, 290, 1720, 950),
        heading_font,
        body_font,
        body_bold,
        small_font,
        score_font,
    )

    latest_scores = [item.latest.score for item in report.series]
    highest_series = max(report.series, key=lambda item: item.latest.score)
    draw.text((110, 1010), "最高", fill="#7a8792", font=small_font)
    draw.text((110, 1044), _score_text(highest_series.latest.score), fill="#17202b", font=score_font)
    draw.text((220, 1052), _short_label(highest_series), fill="#667785", font=body_font)
    draw.text((460, 1010), "中位数", fill="#7a8792", font=small_font)
    draw.text((460, 1044), _score_text(float(median(latest_scores))), fill="#17202b", font=score_font)
    draw.text((570, 1052), f"{len(report.series)} 个配置", fill="#667785", font=body_font)
    draw.text((810, 1010), "测试成本", fill="#7a8792", font=small_font)
    cost_text = f"${report.total_cost_usd:.2f}" if report.total_cost_usd is not None else "--"
    draw.text((810, 1044), cost_text, fill="#17202b", font=score_font)
    draw.text((1400, 1028), "数据来源：codexradar.com/current.json", fill="#667785", font=body_font)
    draw.text((1536, 1064), "视觉重绘，不代表官方图表", fill="#8a94a6", font=small_font)

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _draw_plot(draw, series, batches, plot, body_font, small_font, body_bold) -> None:
    left, top, right, bottom = plot
    draw.text((48, top - 30), "IQ", fill="#63717c", font=body_bold)
    ticks = (30, 60, 75, 90, 105, 120, 135, 150)
    for score in ticks:
        y = _score_y(score, top, bottom)
        draw.line((left, y, right, y), fill="#d5dce2", width=2)
        draw.text((88, y), str(score), fill="#667785", font=body_font, anchor="rm")
    for index, batch in enumerate(batches):
        x = _batch_x(index, len(batches), left, right)
        draw.line((x, top, x, bottom), fill="#dfe5ea", width=1)
        draw.text((x, bottom + 22), _batch_label(batch), fill="#556672", font=body_font, anchor="ma")

    for item in series:
        color = _series_color(item)
        points_by_slot = {_batch_slot(point.batch): point for point in item.points}
        coords: list[tuple[int, int]] = []
        for index, batch in enumerate(batches):
            point = points_by_slot.get(batch)
            if point is None:
                continue
            coords.append((_batch_x(index, len(batches), left, right), _score_y(point.score, top, bottom)))
        if len(coords) >= 2:
            _line(draw, coords, color, solid=item.reasoning_effort.casefold() == "max")
        for x, y in coords:
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill="#f3f6f9", outline=color, width=3)
    draw.text((110, 968), "粗实线：Max · 虚线：其他推理强度 · 右侧颜色与曲线一致", fill="#7a8792", font=small_font)


def _draw_ranking(draw, series, box, heading_font, body_font, body_bold, small_font, score_font) -> None:
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=10, fill="#172229")
    draw.text((left + 30, top + 30), "最新一轮", fill="#9cabb5", font=small_font)
    draw.text((left + 30, top + 66), "配置排名", fill="#f7f8f9", font=heading_font)
    rows = sorted(series, key=lambda item: (-item.latest.score, item.label.casefold()))
    row_y = top + 132
    row_height = 57
    for index, item in enumerate(rows):
        if row_y + row_height > bottom - 10:
            break
        color = _series_color(item)
        draw.ellipse((left + 30, row_y + 7, left + 42, row_y + 19), fill=color)
        draw.text((left + 56, row_y), _short_label(item), fill=color, font=body_bold)
        point = item.latest
        detail = f"{point.passed}/{point.tasks}"
        if point.cost_usd is not None:
            detail += f" · ${point.cost_usd:.2f}"
        draw.text((left + 56, row_y + 27), detail, fill="#8f9da7", font=small_font)
        draw.text((right - 30, row_y - 2), _score_text(point.score), fill=color, font=score_font, anchor="ra")
        if index < len(rows) - 1:
            draw.line((left + 30, row_y + 48, right - 30, row_y + 48), fill="#34434b", width=1)
        row_y += row_height


def _visible_batches(series: tuple[RadarSeries, ...]) -> tuple[str, ...]:
    batches = {_batch_slot(point.batch) for item in series for point in item.points}
    return tuple(sorted(batches, key=_batch_sort_key)[-7:])


def _batch_slot(batch: str) -> str:
    parts = batch.casefold().split("-")
    if len(parts) < 4:
        return batch
    period = parts[3].split("_", 1)[0]
    return "-".join((*parts[:3], period))


def _batch_sort_key(batch: str) -> tuple[str, int, int]:
    slot = _batch_slot(batch)
    parts = slot.split("-")
    date = "-".join(parts[:3]) if len(parts) >= 3 else slot
    period = parts[3] if len(parts) >= 4 else ""
    order = {"am": 0, "pm": 1, "n": 2}.get(period, 3)
    suffix = 0
    if "_" in batch:
        try:
            suffix = int(batch.rsplit("_", 1)[1])
        except ValueError:
            pass
    return date, order, suffix


def _batch_label(batch: str) -> str:
    parts = _batch_slot(batch).split("-")
    if len(parts) < 4:
        return batch
    period = {"am": "上午", "pm": "下午", "n": "夜间"}.get(parts[3], parts[3])
    return f"{parts[1]}-{parts[2]} {period}"


def _score_y(score: float, top: int, bottom: int) -> int:
    minimum, maximum = 30.0, 155.0
    ratio = (max(minimum, min(maximum, score)) - minimum) / (maximum - minimum)
    return round(bottom - ratio * (bottom - top))


def _batch_x(index: int, count: int, left: int, right: int) -> int:
    return left if count <= 1 else round(left + index * (right - left) / (count - 1))


def _line(draw, coords: list[tuple[int, int]], color: str, *, solid: bool) -> None:
    if solid:
        draw.line(coords, fill=color, width=5, joint="curve")
        return
    for start, end in zip(coords, coords[1:]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        distance = max(1.0, math.hypot(dx, dy))
        step = 16
        dash = 10
        position = 0.0
        while position < distance:
            stop = min(distance, position + dash)
            p1 = (round(start[0] + dx * position / distance), round(start[1] + dy * position / distance))
            p2 = (round(start[0] + dx * stop / distance), round(start[1] + dy * stop / distance))
            draw.line((p1, p2), fill=color, width=3)
            position += step


def _series_color(item: RadarSeries) -> str:
    key = f"{item.model} {item.reasoning_effort}".casefold()
    colors = {
        "sol xhigh": "#7c3fd1",
        "luna max": "#db3975",
        "sol high": "#e7521c",
        "sol low": "#344b56",
        "terra max": "#0785c1",
        "sol max": "#d99a00",
        "sol medium": "#168f65",
        "terra medium": "#4058bd",
        "luna medium": "#9e244c",
    }
    for marker, color in colors.items():
        if marker in key:
            return color
    palette = ("#2563eb", "#dc2626", "#059669", "#9333ea", "#ca8a04")
    return palette[sum(ord(char) for char in key) % len(palette)]


def _short_label(item: RadarSeries) -> str:
    model = item.model.casefold().replace("gpt-5.6-", "").replace("gpt-5-", "")
    return f"{model.title()} {item.reasoning_effort}".strip()


def _series_label(model: str, effort: str) -> str:
    return f"{model} {effort}".strip()


def _score_text(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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
