from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from backend.app.status_bars import ConfigStatusBarsData
from backend.app.time_utils import coerce_aware_utc, utc_now


STATE_COLORS = {
    "unknown": "#cbd5e1",
    "ok": "#22c55e",
    "partial": "#f59e0b",
    "down": "#ef4444",
}

STATE_LABELS = {
    "unknown": "未检查",
    "ok": "200可用",
    "partial": "部分可用",
    "down": "不可用",
}

STATUS_LABELS = {
    "ok": "可用",
    "down": "中断",
    "unknown": "未知",
}


def render_status_image(
    configs: list[ConfigStatusBarsData],
    *,
    timezone_name: str,
    generated_at: datetime | None = None,
) -> bytes:
    now = coerce_aware_utc(generated_at or utc_now()).astimezone(ZoneInfo(timezone_name))
    width = 1120
    header_height = 112
    config_height = 164
    footer_height = 34
    height = header_height + max(1, len(configs)) * config_height + footer_height

    image = Image.new("RGB", (width, height), "#f8fafc")
    draw = ImageDraw.Draw(image)
    font_title = _load_font(34, bold=True)
    font_heading = _load_font(22, bold=True)
    font_regular = _load_font(17)
    font_small = _load_font(14)
    font_tiny = _load_font(12)

    draw.rectangle((0, 0, width, 88), fill="#0f172a")
    draw.text((32, 22), "APIMonitorBot 状态图", fill="#f8fafc", font=font_title)
    draw.text(
        (32, 64),
        f"生成时间 {now:%Y-%m-%d %H:%M:%S} {timezone_name}",
        fill="#cbd5e1",
        font=font_tiny,
    )
    _draw_legend(draw, width - 424, 28, font_small)

    y = header_height
    if not configs:
        draw.text((32, y + 30), "没有可显示的 API 配置", fill="#475569", font=font_heading)
    for config in configs:
        _draw_config_block(draw, config, 32, y, width - 64, font_heading, font_regular, font_small, font_tiny)
        y += config_height

    draw.text((32, height - 26), "灰色=未检查  绿色=可用  黄色=部分时间可用  红色=不可用", fill="#64748b", font=font_tiny)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _draw_legend(draw: ImageDraw.ImageDraw, x: int, y: int, font: ImageFont.ImageFont) -> None:
    cursor = x
    for key in ("unknown", "ok", "partial", "down"):
        draw.rounded_rectangle((cursor, y + 4, cursor + 18, y + 18), radius=4, fill=STATE_COLORS[key])
        draw.text((cursor + 24, y), STATE_LABELS[key], fill="#e2e8f0", font=font)
        cursor += 98


def _draw_config_block(
    draw: ImageDraw.ImageDraw,
    config: ConfigStatusBarsData,
    x: int,
    y: int,
    width: int,
    font_heading: ImageFont.ImageFont,
    font_regular: ImageFont.ImageFont,
    font_small: ImageFont.ImageFont,
    font_tiny: ImageFont.ImageFont,
) -> None:
    card_height = 144
    draw.rounded_rectangle((x, y, x + width, y + card_height), radius=12, fill="#ffffff", outline="#e2e8f0", width=1)
    title = _fit_text(config.config_name, font_heading, 420)
    draw.text((x + 22, y + 18), title, fill="#0f172a", font=font_heading)
    status_text = STATUS_LABELS.get(config.status, "未知")
    status_color = STATE_COLORS["ok"] if config.status == "ok" else STATE_COLORS["down"] if config.status == "down" else STATE_COLORS["unknown"]
    pill_x = x + 448
    draw.rounded_rectangle((pill_x, y + 18, pill_x + 88, y + 46), radius=14, fill=status_color)
    _draw_text_centered_y(
        draw,
        (pill_x + 18, y + 32),
        status_text,
        font_small,
        fill="#ffffff" if config.status != "unknown" else "#334155",
    )

    meta = config.model_name
    if config.last_code:
        meta += f" · {config.last_code}"
    draw.text((x + 22, y + 52), _fit_text(meta, font_small, 520), fill="#64748b", font=font_small)
    draw.text((x + width - 188, y + 22), f"最近请求成功率 {config.success_rate:.1f}%", fill="#0f172a", font=font_regular)

    row_y = y + 78
    for window in config.windows:
        bucket_height = 14
        center_y = row_y + bucket_height / 2
        _draw_text_centered_y(draw, (x + 22, center_y), window.label, font_small, fill="#334155")
        _draw_buckets(draw, window.buckets, x + 132, row_y, 700)
        _draw_text_centered_y(
            draw,
            (x + 850, center_y),
            f"每 {window.bucket_minutes} 分钟一格",
            font_tiny,
            fill="#94a3b8",
        )
        row_y += 24


def _draw_text_centered_y(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    *,
    fill: str,
) -> None:
    x, center_y = position
    bbox = draw.textbbox((0, 0), text, font=font)
    text_y = center_y - (bbox[3] - bbox[1]) / 2 - bbox[1]
    draw.text((int(x), int(round(text_y))), text, fill=fill, font=font)


def _draw_buckets(draw: ImageDraw.ImageDraw, buckets, x: int, y: int, max_width: int) -> None:
    if not buckets:
        return
    gap = 3
    bar_width = max(7, min(22, (max_width - gap * (len(buckets) - 1)) // len(buckets)))
    cursor = x
    for bucket in buckets:
        draw.rounded_rectangle(
            (cursor, y, cursor + bar_width, y + 14),
            radius=3,
            fill=STATE_COLORS.get(bucket.state, STATE_COLORS["unknown"]),
        )
        cursor += bar_width + gap


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
