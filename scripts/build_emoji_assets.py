"""Build a compact offline Twemoji atlas from the pinned upstream release."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import zipfile

import httpx
from PIL import Image

VERSION = "17.0.3"
ROOT = Path(__file__).resolve().parents[1] / "backend" / "app" / "assets"
URL = f"https://codeload.github.com/jdecked/twemoji/zip/refs/tags/v{VERSION}"


def main():
    response = httpx.get(URL, follow_redirects=True, timeout=90)
    response.raise_for_status()
    if len(response.content) > 64 * 1024 * 1024:
        raise ValueError("Upstream archive too large")
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        names = sorted(name for name in archive.namelist() if re.fullmatch(r"[^/]+/assets/72x72/[0-9a-f-]+\.png", name))
        if not 3000 <= len(names) <= 5000:
            raise ValueError("Unexpected emoji count")
        side, columns = 48, 64
        atlas = Image.new("RGBA", (side * columns, side * ((len(names) + columns - 1) // columns)))
        index = {}
        for number, name in enumerate(names):
            glyph = Image.open(BytesIO(archive.read(name))).convert("RGBA")
            if glyph.size != (72, 72):
                raise ValueError("Unexpected glyph dimensions")
            atlas.paste(glyph.resize((side, side), Image.Resampling.LANCZOS), (number % columns * side, number // columns * side))
            index["-".join(part for part in Path(name).stem.split("-") if part != "fe0f")] = number
        license_name = next(name for name in archive.namelist() if name.endswith("/LICENSE-GRAPHICS"))
        license_text = "\n".join(line.rstrip() for line in archive.read(license_name).decode("utf-8").splitlines()) + "\n"
        (ROOT / "Twemoji-LICENSE.txt").write_text(license_text, encoding="utf-8")
        atlas.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE).save(ROOT / "emoji-atlas.png", optimize=True)
        (ROOT / "emoji-atlas.json").write_text(json.dumps({"version": VERSION, "source": URL,
            "source_sha256": hashlib.sha256(response.content).hexdigest(), "size": side, "columns": columns,
            "glyphs": index}, separators=(",", ":")), encoding="utf-8")
        print(f"Built {len(index)} offline emoji glyphs")


if __name__ == "__main__":
    main()
