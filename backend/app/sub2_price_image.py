from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from backend.app.sub2_rates import Sub2RateChange, Sub2StoredRate
from backend.app.sub2api import format_rate, platform_label
from backend.app.time_utils import coerce_aware_utc


@dataclass(slots=True)
class Sub2PriceBoard:
    name: str
    rates: list[Sub2StoredRate]
    changes: list[Sub2RateChange] = field(default_factory=list)


def render_sub2_price_image(
    boards: list[Sub2PriceBoard],
    *,
    title: str = "Sub2API 渠道倍率",
    timezone_name: str = "Asia/Shanghai",
) -> bytes:
    width = 1240
    header_height = 100
    board_heights = [_measure_board_height(board) for board in boards] or [116]
    height = header_height + sum(board_heights) + 36
    image = Image.new("RGB", (width, height), "#f8fafc")
    draw = ImageDraw.Draw(image)
    font_title = _load_font(34, bold=True)
    font_heading = _load_font(24, bold=True)
    font_regular = _load_font(17)
    font_rate = _load_font(28, bold=True)
    font_small = _load_font(14)
    font_tiny = _load_font(12)

    draw.rectangle((0, 0, width, 78), fill="#111827")
    draw.text((30, 22), title, fill="#f8fafc", font=font_title)
    _draw_legend(draw, width - 366, 26, font_small)

    y = header_height
    if not boards:
        draw.text((30, y + 20), "没有可显示的 Sub2API 价格表", fill="#475569", font=font_heading)
    for board, board_height in zip(boards, board_heights):
        _draw_board(
            draw,
            board,
            30,
            y,
            width - 60,
            board_height - 18,
            timezone_name,
            font_heading,
            font_regular,
            font_rate,
            font_small,
            font_tiny,
        )
        y += board_height

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _measure_board_height(board: Sub2PriceBoard) -> int:
    groups = _rates_by_platform(board.rates)
    rate_rows = sum((len(items) + 1) // 2 for items in groups.values())
    change_rows = min(len(board.changes), 4)
    change_block = change_rows * 28 + (16 if board.changes else 0)
    return 96 + change_block + max(1, rate_rows) * 154 + len(groups) * 42


def _draw_board(
    draw: ImageDraw.ImageDraw,
    board: Sub2PriceBoard,
    x: int,
    y: int,
    width: int,
    height: int,
    timezone_name: str,
    font_heading: ImageFont.ImageFont,
    font_regular: ImageFont.ImageFont,
    font_rate: ImageFont.ImageFont,
    font_small: ImageFont.ImageFont,
    font_tiny: ImageFont.ImageFont,
) -> None:
    draw.rounded_rectangle((x, y, x + width, y + height), radius=12, fill="#ffffff", outline="#e2e8f0", width=1)
    heading = f"【{board.name}】"
    if board.changes:
        heading += " 出现价格变动"
    draw.text((x + 22, y + 18), heading, fill="#0f172a", font=font_heading)

    cursor_y = y + 58
    change_keys = {change.identity for change in board.changes}
    if board.changes:
        for change in board.changes[:4]:
            text = (
                f"{platform_label(change.platform)}: {change.group_name} "
                f"{format_rate(change.old_rate)} -> {format_rate(change.new_rate)} "
                f"({_format_percent(_change_percent(change.old_rate, change.new_rate))})"
            )
            color = _trend_color(_change_percent(change.old_rate, change.new_rate))
            draw.text((x + 24, cursor_y), _fit_text(text, font_regular, width - 48), fill=color, font=font_regular)
            cursor_y += 28
        if len(board.changes) > 4:
            draw.text((x + 24, cursor_y), f"还有 {len(board.changes) - 4} 个分组发生变化", fill="#64748b", font=font_small)
            cursor_y += 24
        cursor_y += 10

    groups = _rates_by_platform(board.rates)
    if not groups:
        draw.text((x + 22, cursor_y), "暂无渠道倍率数据", fill="#64748b", font=font_regular)
        return

    for platform, rates in groups.items():
        color = _platform_color(platform)
        label = platform_label(platform)
        draw.rounded_rectangle((x + 22, cursor_y + 2, x + 138, cursor_y + 30), radius=14, fill=color)
        draw.text((x + 44, cursor_y + 7), label, fill="#ffffff", font=font_small)
        cursor_y += 42
        column_width = (width - 58) // 2
        for index, rate in enumerate(rates):
            col = index % 2
            row = index // 2
            card_x = x + 22 + col * column_width
            card_y = cursor_y + row * 146
            _draw_rate_card(
                draw,
                rate,
                card_x,
                card_y,
                column_width - 14,
                124,
                timezone_name,
                changed=rate.identity in change_keys,
                platform_color=color,
                font_regular=font_regular,
                font_rate=font_rate,
                font_small=font_small,
                font_tiny=font_tiny,
            )
        cursor_y += ((len(rates) + 1) // 2) * 146 + 10


def _draw_rate_card(
    draw: ImageDraw.ImageDraw,
    rate: Sub2StoredRate,
    x: int,
    y: int,
    width: int,
    height: int,
    timezone_name: str,
    *,
    changed: bool,
    platform_color: str,
    font_regular: ImageFont.ImageFont,
    font_rate: ImageFont.ImageFont,
    font_small: ImageFont.ImageFont,
    font_tiny: ImageFont.ImageFont,
) -> None:
    percent = rate.change_percent
    trend_color = _trend_color(percent) if percent is not None else platform_color
    outline = trend_color if changed else "#e2e8f0"
    fill = "#fff7ed" if changed and percent is not None and percent > 0 else "#f0fdf4" if changed and percent is not None and percent < 0 else "#f8fafc"
    draw.rounded_rectangle((x, y, x + width, y + height), radius=9, fill=fill, outline=outline, width=2 if changed else 1)

    name = _fit_text(rate.group_name, font_regular, width - 28)
    draw.text((x + 14, y + 12), name, fill="#334155", font=font_regular)

    current = format_rate(rate.rate_multiplier)
    draw.text((x + 14, y + 42), current, fill=trend_color, font=font_rate)
    percent_text = _format_percent(percent)
    percent_x = x + 20 + _text_width(current, font_rate)
    draw.text((percent_x, y + 50), percent_text, fill=trend_color if percent is not None else "#64748b", font=font_small)

    if rate.previous_rate is not None:
        previous_text = f"上次 {format_rate(rate.previous_rate)}"
    else:
        previous_text = "当前基准"
    draw.text((x + 14, y + 82), previous_text, fill="#64748b", font=font_tiny)

    chart_x = x + max(214, min(254, width // 2))
    chart_y = y + 38
    chart_width = width - (chart_x - x) - 18
    chart_height = 56
    _draw_sparkline(draw, rate, chart_x, chart_y, chart_width, chart_height, trend_color, timezone_name, font_tiny)


def _draw_sparkline(
    draw: ImageDraw.ImageDraw,
    rate: Sub2StoredRate,
    x: int,
    y: int,
    width: int,
    height: int,
    color: str,
    timezone_name: str,
    font_tiny: ImageFont.ImageFont,
) -> None:
    points = _downsample_points(list(rate.history), 48)
    draw.line((x, y + height, x + width, y + height), fill="#cbd5e1", width=1)
    if not points:
        return
    values = [point.rate_multiplier for point in points]
    minimum = min(values)
    maximum = max(values)
    if math.isclose(minimum, maximum, rel_tol=0, abs_tol=1e-12):
        y_value = y + height // 2
        draw.line((x, y_value, x + width, y_value), fill=color, width=3)
        draw.ellipse((x - 3, y_value - 3, x + 3, y_value + 3), fill=color)
        draw.ellipse((x + width - 3, y_value - 3, x + width + 3, y_value + 3), fill=color)
    else:
        coords: list[tuple[float, float]] = []
        for index, point in enumerate(points):
            px = x + (width * index / max(1, len(points) - 1))
            ratio = (point.rate_multiplier - minimum) / (maximum - minimum)
            py = y + height - ratio * height
            coords.append((px, py))
        draw.line(coords, fill=color, width=3, joint="curve")
        for px, py in (coords[0], coords[-1]):
            draw.ellipse((px - 3, py - 3, px + 3, py + 3), fill=color)

    start = _format_axis_date(points[0].recorded_at, timezone_name)
    end = _format_axis_date(points[-1].recorded_at, timezone_name)
    draw.text((x, y + height + 8), start, fill="#64748b", font=font_tiny)
    end_width = _text_width(end, font_tiny)
    draw.text((x + width - end_width, y + height + 8), end, fill="#64748b", font=font_tiny)


def _draw_legend(draw: ImageDraw.ImageDraw, x: int, y: int, font: ImageFont.ImageFont) -> None:
    items = [("#ef4444", "上涨"), ("#22c55e", "下跌"), ("#64748b", "基准")]
    cursor = x
    for color, label in items:
        draw.rounded_rectangle((cursor, y + 4, cursor + 18, y + 18), radius=4, fill=color)
        draw.text((cursor + 24, y), label, fill="#e2e8f0", font=font)
        cursor += 106


def _rates_by_platform(rates: list[Sub2StoredRate]) -> dict[str, list[Sub2StoredRate]]:
    grouped: dict[str, list[Sub2StoredRate]] = {}
    for rate in sorted(rates, key=lambda item: (platform_label(item.platform), item.group_name)):
        grouped.setdefault(rate.platform, []).append(rate)
    return grouped


def _platform_color(platform: str) -> str:
    clean = platform.lower()
    if clean == "anthropic":
        return "#f97316"
    if clean == "openai":
        return "#22c55e"
    return "#38bdf8"


def _trend_color(percent: float | None) -> str:
    if percent is None:
        return "#64748b"
    if percent > 0:
        return "#ef4444"
    if percent < 0:
        return "#22c55e"
    return "#64748b"


def _change_percent(old_rate: float, new_rate: float) -> float | None:
    if math.isclose(old_rate, 0, rel_tol=0, abs_tol=1e-12):
        return None
    return (new_rate - old_rate) / old_rate * 100


def _format_percent(percent: float | None) -> str:
    if percent is None:
        return "基准"
    sign = "+" if percent > 0 else ""
    return f"{sign}{percent:.1f}%"


def _format_axis_date(value: datetime, timezone_name: str) -> str:
    local = coerce_aware_utc(value).astimezone(ZoneInfo(timezone_name))
    return local.strftime("%m-%d")


def _downsample_points(points: list, limit: int) -> list:
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[round(index * step)] for index in range(limit)]


def _load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _fit_text(text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    if _text_width(text, font) <= max_width:
        return text
    ellipsis = "..."
    trimmed = text
    while trimmed and _text_width(trimmed + ellipsis, font) > max_width:
        trimmed = trimmed[:-1]
    return trimmed + ellipsis if trimmed else ellipsis


def _text_width(text: str, font: ImageFont.ImageFont) -> int:
    bbox = font.getbbox(text)
    return int(bbox[2] - bbox[0])
