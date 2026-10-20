"""Offline, platform-independent emoji layout using the bundled Twemoji atlas."""
from functools import lru_cache
import json
from pathlib import Path
import unicodedata

import emoji
from PIL import Image

ASSETS = Path(__file__).with_name("assets")


@lru_cache(maxsize=1)
def atlas_index():
    return json.loads((ASSETS / "emoji-atlas.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def atlas_image():
    with Image.open(ASSETS / "emoji-atlas.png") as source:
        return source.convert("RGBA")


def runs(text):
    cursor = 0
    for item in emoji.emoji_list(text):
        start, end = item["match_start"], item["match_end"]
        if start > cursor:
            yield text[cursor:start], False
        yield text[start:end], True
        cursor = end
    if cursor < len(text):
        yield text[cursor:], False


def text_atoms(text):
    for value, is_emoji in runs(text):
        if is_emoji:
            yield value
        else:
            atom = ""
            for char in value:
                if unicodedata.combining(char) and atom:
                    atom += char
                else:
                    if atom:
                        yield atom
                    atom = char
            if atom:
                yield atom


def key_for(value):
    return "-".join(f"{ord(char):x}" for char in value if ord(char) != 0xFE0F)


def fallback(value):
    return "[" + emoji.demojize(value).strip(":").replace("_", " ") + "]"


def run_width(value, is_emoji, font):
    if not is_emoji:
        return font.getlength(value)
    return round(font.size * 1.08) if key_for(value) in atlas_index()["glyphs"] else font.getlength(fallback(value))


def text_width(text, font):
    return round(sum(run_width(value, is_emoji, font) for value, is_emoji in runs(text)))


@lru_cache(maxsize=512)
def glyph(value, size):
    metadata = atlas_index()
    index = metadata["glyphs"][key_for(value)]
    side, columns = metadata["size"], metadata["columns"]
    x, y = index % columns * side, index // columns * side
    return atlas_image().crop((x, y, x + side, y + side)).resize((size, size), Image.Resampling.LANCZOS)


def draw_text(image, raw, xy, text, **kwargs):
    font = kwargs["font"]
    anchor = kwargs.pop("anchor", None) or "la"
    x, y = xy
    width = text_width(text, font)
    if anchor[0] == "r":
        x -= width
    elif anchor[0] == "m":
        x -= width / 2
    for value, is_emoji in runs(text):
        if is_emoji and key_for(value) in atlas_index()["glyphs"]:
            size = round(font.size * 1.08)
            top = font.getbbox("中", anchor="l" + anchor[1])[1] + (font.size - size) / 2
            bitmap = glyph(value, size)
            image.paste(bitmap, (round(x), round(y + top)), bitmap)
        else:
            raw.text((x, y), fallback(value) if is_emoji else value, anchor="l" + anchor[1], **kwargs)
        x += run_width(value, is_emoji, font)
