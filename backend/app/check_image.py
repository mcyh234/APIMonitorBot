from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from backend.app.time_utils import coerce_aware_utc, utc_now


@dataclass(frozen=True, slots=True)
class CheckResultImageRow:
    name: str
    ok: bool
    code: str
    success_rate: float
    latency_ms: int | None = None


def render_check_result_image(
    rows: list[CheckResultImageRow],
    *,
    timezone_name: str,
    generated_at: datetime | None = None,
) -> bytes:
    now = coerce_aware_utc(generated_at or utc_now()).astimezone(ZoneInfo(timezone_name))
    width = 980
    header_height = 126
    row_height = 86
    footer_height = 34
    height = header_height + max(1, len(rows)) * row_height + footer_height

    image = Image.new("RGB", (width, height), "#f8fafc")
    draw = ImageDraw.Draw(image)
    font_title = _load_font(32, bold=True)
    font_heading = _load_font(21, bold=True)
    font_regular = _load_font(17)
    font_small = _load_font(14)
    font_tiny = _load_font(12)

    ok_count = sum(1 for row in rows if row.ok)
    down_count = len(rows) - ok_count

    draw.rectangle((0, 0, width, 94), fill="#0f172a")
    draw.text((30, 20), "APIMonitorBot API 检查结果", fill="#f8fafc", font=font_title)
    draw.text(
        (30, 64),
        f"生成时间 {now:%Y-%m-%d %H:%M:%S} {timezone_name}",
        fill="#cbd5e1",
        font=font_tiny,
    )
    summary = f"共 {len(rows)} 个 · 可用 {ok_count} 个 · 不可用 {down_count} 个"
    draw.text((width - _text_width(summary, font_regular) - 30, 36), summary, fill="#e2e8f0", font=font_regular)

    y = header_height
    if not rows:
        draw.text((30, y + 22), "当前通知对象没有绑定 API 检测任务。", fill="#475569", font=font_heading)
    for row in rows:
        _draw_row(draw, row, 30, y, width - 60, font_heading, font_regular, font_small)
        y += row_height

    draw.text(
        (30, height - 26),
        "绿色=服务可用  红色=服务不可用  成功率基于最近请求记录",
        fill="#64748b",
        font=font_tiny,
    )
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _draw_row(
    draw: ImageDraw.ImageDraw,
    row: CheckResultImageRow,
    x: int,
    y: int,
    width: int,
    font_heading: ImageFont.ImageFont,
    font_regular: ImageFont.ImageFont,
    font_small: ImageFont.ImageFont,
) -> None:
    status_color = "#22c55e" if row.ok else "#ef4444"
    soft_color = "#dcfce7" if row.ok else "#fee2e2"
    text_color = "#166534" if row.ok else "#991b1b"
    status_text = "服务可用" if row.ok else "服务不可用"

    draw.rounded_rectangle((x, y, x + width, y + 68), radius=12, fill="#ffffff", outline="#e2e8f0", width=1)
    draw.rectangle((x, y, x + 6, y + 68), fill=status_color)
    draw.text((x + 22, y + 16), _fit_text(row.name, font_heading, 360), fill="#0f172a", font=font_heading)

    pill_x = x + 410
    draw.rounded_rectangle((pill_x, y + 18, pill_x + 116, y + 48), radius=15, fill=soft_color)
    _draw_text_centered(draw, (pill_x, y + 18, pill_x + 116, y + 48), status_text, font_small, fill=text_color)

    code_text = f"状态码 {row.code or 'UNKNOWN'}"
    draw.text((x + 550, y + 22), code_text, fill="#334155", font=font_regular)

    rate_text = f"最近请求成功率 {row.success_rate:.1f}%"
    draw.text((x + width - _text_width(rate_text, font_regular) - 22, y + 22), rate_text, fill="#0f172a", font=font_regular)

    if row.latency_ms is not None:
        latency_text = f"{row.latency_ms} ms"
        draw.text((x + 22, y + 45), latency_text, fill="#94a3b8", font=font_small)


def _draw_text_centered(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font: ImageFont.ImageFont,
    *,
    fill: str,
) -> None:
    left, top, right, bottom = box
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = left + (right - left - text_width) / 2 - bbox[0]
    y = top + (bottom - top - text_height) / 2 - bbox[1]
    draw.text((int(round(x)), int(round(y))), text, fill=fill, font=font)


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
