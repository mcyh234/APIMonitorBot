"""Per-render Material 3 inspired tonal roles shared by generated reports."""
from __future__ import annotations

import colorsys
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from backend.app.time_utils import coerce_aware_utc, utc_now

_lock = threading.Lock()
_last_hue: int | None = None


@lru_cache(maxsize=96)
def load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(Path(__file__).with_name("assets") / "NotoSansSC-VF.ttf"), size=size)
    font.set_variation_by_axes([700 if bold else 400])
    return font


def _tone(hue: int, lightness: float, saturation: float) -> str:
    rgb = colorsys.hls_to_rgb(hue / 360, lightness, saturation)
    return "#" + "".join(f"{round(component * 255):02x}" for component in rgb)


@dataclass(frozen=True)
class MaterialTheme:
    hue: int
    dark: bool
    surface: str
    container: str
    container_high: str
    on_surface: str
    on_surface_variant: str
    outline: str
    primary: str
    on_primary: str
    primary_container: str
    on_primary_container: str


def new_theme(at: datetime | None = None, *, hue: int | None = None) -> MaterialTheme:
    global _last_hue
    local = coerce_aware_utc(at or utc_now()).astimezone(ZoneInfo("Asia/Shanghai"))
    dark = not 7 <= local.hour < 19
    if hue is None:
        with _lock:
            hue = secrets.randbelow(360) if _last_hue is None else (_last_hue + 65 + secrets.randbelow(230)) % 360
            _last_hue = hue
    return MaterialTheme(
        hue, dark,
        _tone(hue, .07 if dark else .98, .12),
        _tone(hue, .115 if dark else .95, .13),
        _tone(hue, .18 if dark else .91, .16),
        _tone(hue, .92 if dark else .10, .08),
        _tone(hue, .77 if dark else .33, .09),
        _tone(hue, .35 if dark else .78, .09),
        _tone(hue, .78 if dark else .32, .50),
        _tone(hue, .13 if dark else .99, .15),
        _tone(hue, .24 if dark else .88, .34),
        _tone(hue, .93 if dark else .15, .24),
    )


class MaterialDraw:
    """Map legacy report inks to roles before rasterization and antialiasing."""

    _text = {"#0f172a", "#111827", "#17202b", "#334155", "#1e293b"}
    _muted = {"#475569", "#64748b", "#94a3b8", "#536471", "#667785", "#7a8792",
              "#63717c", "#556672", "#8a94a6", "#8f9da7", "#9cabb5"}
    _lines = {"#e2e8f0", "#cbd5e1", "#c9d1d8", "#d5dce2", "#dfe5ea", "#34434b"}
    _surface = {"#ffffff", "#f8fafc", "#f3f6f9", "#f1f5f9", "#f7f8f9"}
    _header = {"#0f172a", "#111827", "#172229"}

    def __init__(self, image: Image.Image, theme: MaterialTheme):
        self.raw = ImageDraw.Draw(image)
        self.theme = theme

    def _ink(self, color, method: str, key: str):
        if not isinstance(color, str):
            return color
        value = color.lower()
        text = method in {"text", "multiline_text"}
        t = self.theme
        if value in {t.surface, t.container, t.container_high, t.on_surface, t.on_surface_variant,
                     t.outline, t.primary, t.on_primary, t.primary_container, t.on_primary_container}:
            return color
        if text:
            if value in self._text:
                return t.on_surface
            if value in self._muted:
                return t.on_surface_variant
            if value in self._surface or value in self._lines:
                return t.on_primary
            if value == "#1d9bf0":
                return t.primary
        else:
            if value in self._header:
                return t.primary_container
            if value in self._surface:
                return t.container
            if value in self._lines:
                return t.outline if key == "outline" or method == "line" else t.container_high
        if t.dark and value.startswith("#") and len(value) == 7:
            r, g, b = (int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))
            h, light, saturation = colorsys.rgb_to_hls(r, g, b)
            if saturation > .2:
                if not text and light <= .85:
                    return color
                target = .76 if text else .23 if light > .85 else max(.55, light)
                return _tone(round(h * 360), target, saturation)
        return color

    def __getattr__(self, name):
        function = getattr(self.raw, name)
        if name not in {"text", "multiline_text", "rectangle", "rounded_rectangle", "ellipse", "line", "polygon", "arc"}:
            return function

        def draw(*args, **kwargs):
            for key in ("fill", "outline", "stroke_fill"):
                if key in kwargs:
                    kwargs[key] = self._ink(kwargs[key], name, key)
            if name == "rounded_rectangle" and kwargs.get("radius", 0) >= 7:
                kwargs["radius"] = max(16, kwargs["radius"])
            return function(*args, **kwargs)
        return draw


def material_canvas(size: tuple[int, int], at: datetime | None = None, *, theme: MaterialTheme | None = None):
    theme = theme or new_theme(at)
    image = Image.new("RGB", size, theme.surface)
    return image, MaterialDraw(image, theme)
