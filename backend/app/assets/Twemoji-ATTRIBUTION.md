# Offline emoji assets

Graphics: Twemoji, copyright Twitter, Inc. and other contributors.
Maintained upstream: https://github.com/jdecked/twemoji/tree/v17.0.3
License: Creative Commons Attribution 4.0 International; see `Twemoji-LICENSE.txt`.

This project resizes the 72×72 graphics to 48×48, quantizes the palette and packs
them into `emoji-atlas.png` for offline rendering. `emoji-atlas.json` records the
pinned source, its SHA-256, and sprite coordinates. No network is used at render time.
Rebuild with `python scripts/build_emoji_assets.py`.
