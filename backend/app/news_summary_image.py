"""Offline Markdown and source-backed timeline rendering, without HTML or downloads."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from io import BytesIO
import re
from zoneinfo import ZoneInfo

from markdown_it import MarkdownIt

from backend.app.emoji_text import text_atoms, text_width
from backend.app.material_theme import load_font, material_canvas
from backend.app.time_utils import utc_now
from backend.app.x_timeline import post_datetime


def markdown_parser():
    return MarkdownIt("commonmark", {"html": False, "maxNesting": 12}).enable(["table", "strikethrough"])


@dataclass(frozen=True)
class Span:
    text: str
    bold: bool = False
    code: bool = False
    accent: bool = False
    strike: bool = False


@dataclass
class Block:
    kind: str
    spans: list[Span]
    size: int = 28
    indent: int = 0
    cells: list[list[Span]] | None = None


def inline_spans(tokens):
    bold = accent = strike = 0
    result = []
    for token in tokens or []:
        if token.type in {"strong_open", "strong_close"}:
            bold += token.nesting
        elif token.type in {"em_open", "em_close", "link_open", "link_close"}:
            accent += token.nesting
        elif token.type in {"s_open", "s_close"}:
            strike += token.nesting
        elif token.type in {"text", "code_inline", "softbreak", "hardbreak"}:
            text = "\n" if token.type == "hardbreak" else " " if token.type == "softbreak" else token.content
            result.append(Span(text, bold > 0, token.type == "code_inline", accent > 0, strike > 0))
        elif token.type == "image":
            raise ValueError("Unreviewed images are not supported")
    return result


def markdown_blocks(markdown):
    result, lists = [], []
    heading, quote, prefix, row, cell = 0, 0, "", None, None
    for token in markdown_parser().parse(markdown):
        kind = token.type
        if kind == "heading_open":
            heading = int(token.tag[1:])
        elif kind == "heading_close":
            heading = 0
        elif kind in {"ordered_list_open", "bullet_list_open"}:
            lists.append(int(token.attrGet("start") or 1) if kind == "ordered_list_open" else None)
        elif kind in {"ordered_list_close", "bullet_list_close"}:
            lists.pop()
        elif kind == "list_item_open":
            prefix = f"{lists[-1]}. " if lists[-1] is not None else "• "
            if lists[-1] is not None:
                lists[-1] += 1
        elif kind in {"blockquote_open", "blockquote_close"}:
            quote += token.nesting
        elif kind == "tr_open":
            row = []
        elif kind in {"th_open", "td_open"}:
            cell = []
        elif kind in {"th_close", "td_close"}:
            row.append(cell)
            cell = None
        elif kind == "tr_close":
            result.append(Block("table", [], 23, cells=row))
            row = None
        elif kind == "inline":
            spans = inline_spans(token.children)
            if cell is not None:
                cell.extend(spans)
            else:
                if prefix:
                    spans.insert(0, Span(prefix))
                size = max(29, 44 - heading * 3) if heading else 28
                if heading:
                    spans = [replace(span, bold=True) for span in spans]
                result.append(Block("heading" if heading else "quote" if quote else "text", spans, size,
                                    max(0, len(lists) - 1) * 28 + (24 if quote else 0)))
                prefix = ""
        elif kind in {"fence", "code_block"}:
            for line in token.content.rstrip("\n").split("\n"):
                result.append(Block("code", [Span(line.expandtabs(4), code=True)], 24))
        elif kind == "hr":
            result.append(Block("rule", []))
    return result


def wrapped(spans, width, size):
    lines, line, x = [], [], 0
    for span in spans:
        font = load_font(size, bold=span.bold)
        atoms = []
        for part in re.findall(r"\[[NM]\d+(?:,\s*[NM]\d+)*\]|[A-Za-z0-9_./:@?&=%+-]+|[^A-Za-z0-9_./:@?&=%+-]+", span.text):
            if "\n" not in part and (part.startswith("[") or part[0].isascii() and not part[0].isspace()) and text_width(part, font) <= width:
                atoms.append(part)
            else:
                atoms.extend(text_atoms(part))
        for atom in atoms:
            length = text_width(atom, font)
            if atom == "\n" or (line and x + length > width and atom not in ")]】）。，、！？：；.!?,;:"):
                lines.append(line); line, x = [], 0
                if atom == "\n":
                    continue
            if line and replace(line[-1], text="") == replace(span, text=""):
                line[-1] = replace(line[-1], text=line[-1].text + atom)
            else:
                line.append(replace(span, text=atom))
            x += length
    if line or not lines:
        lines.append(line)
    return lines


def source_time(source):
    return post_datetime(source.get("published_at")) or post_datetime(source.get("delivered_at")) or datetime(1970, 1, 1, tzinfo=ZoneInfo("UTC"))


def time_label(source):
    return source_time(source).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M")


def document_blocks(document, sources):
    blocks = markdown_blocks(document.markdown)
    for event in document.timelines:
        blocks.append(Block("heading", [Span("事件时间线 · " + event.title, bold=True)], 35))
        for node in sorted(event.nodes, key=lambda item: min(source_time(sources[key]) for key in item.source_ids)):
            date = min(node.source_ids, key=lambda key: source_time(sources[key]))
            flags = [bool(sources[key].get("historical")) for key in node.source_ids]
            period = "历史" if all(flags) else "历史与本期" if any(flags) else "本期"
            blocks.append(Block("timeline", [Span(time_label(sources[date]) + f" · {period}" + (" · 送达时间" if not sources[date].get("published_at") else ""), bold=True, accent=True)], 24))
            blocks.append(Block("timeline", [Span(node.text + "  [" + ", ".join(node.source_ids) + "]")], 27, 28))
    if document.connections:
        blocks.append(Block("heading", [Span("与历史新闻的关联", bold=True)], 35))
        for connection in document.connections:
            refs = ", ".join(connection.current_ids + connection.history_ids)
            blocks.append(Block("quote", [Span(connection.relation + " · ", bold=True, accent=True), Span(connection.explanation + f"  [{refs}]")], 27, 24))
    blocks.append(Block("heading", [Span("来源索引 · 时间均为北京时间", bold=True)], 32))
    used_memory = {key for event in document.timelines for node in event.nodes for key in node.source_ids if sources[key].get("historical")}
    used_memory |= {key for connection in document.connections for key in connection.history_ids}
    for key, source in sources.items():
        if source.get("historical") and key not in used_memory:
            continue
        blocks.append(Block("source", [Span(f"[{key}] @{source['account']} · {time_label(source)}" + (" · 历史记忆" if source.get("historical") else "") + (" · 送达时间" if not source.get("published_at") else ""), bold=True)], 23))
        blocks.append(Block("source", [Span(source["url"], accent=True)], 21))
    return blocks


def render_summary_images(document, sources, *, generated_at=None):
    at = generated_at or utc_now()
    width, content_width, page_limit = 1440, 1280, 5000
    units = []
    for block in document_blocks(document, sources):
        if block.cells is not None:
            if len(block.cells) > 6:
                for index, cell in enumerate(block.cells):
                    for line in wrapped([Span(f"列 {index + 1}：", bold=True), *cell], content_width - 24, block.size):
                        units.append((Block("table", line, block.size), None, 38))
            else:
                column = content_width / max(1, len(block.cells))
                cells = [wrapped(cell, column - 28, block.size) for cell in block.cells]
                height = max((len(cell) for cell in cells), default=1) * 34 + 22
                units.append((block, cells, height))
        elif block.kind == "rule":
            units.append((block, None, 24))
        else:
            for index, line in enumerate(wrapped(block.spans, content_width - block.indent - 28, block.size)):
                units.append((replace(block, spans=line), None, block.size + 16 + (12 if index == 0 and block.kind == "heading" else 0)))
            units.append((Block("space", []), None, 8))
    pages, page, height = [], [], 0
    for unit in units:
        if unit[2] > page_limit - 320:
            raise ValueError("Summary block is too tall")
        if page and height + unit[2] > page_limit - 320:
            pages.append((page, height)); page, height = [], 0
        page.append(unit); height += unit[2]
    if page:
        pages.append((page, height))
    if len(pages) > 8:
        raise ValueError("Summary exceeds page limit")
    result = []
    title_lines = wrapped([Span(document.title, bold=True)], content_width, 42)
    # Reserve title space in addition to the body budget for every page.
    for index, (units, body_height) in enumerate(pages):
        image, draw = material_canvas((width, int(body_height + 320 + 56 * (len(title_lines) - 1))), at)
        theme = draw.theme
        draw.rounded_rectangle((48, 40, width - 48, image.height - 40), radius=24, fill=theme.container)
        draw.text((80, 62), "AI NEWS  /  新闻总结", font=load_font(24, bold=True), fill=theme.primary)
        y = 108
        def paint(spans, x, y, size):
            for span in spans:
                font = load_font(size, bold=span.bold)
                length = text_width(span.text, font)
                if span.code:
                    draw.rectangle((x - 2, y, x + length + 2, y + size + 10), fill=theme.container_high)
                draw.text((x, y), span.text, font=font, fill=theme.primary if span.accent else theme.on_surface)
                if span.strike:
                    draw.line((x, y + size * .65, x + length, y + size * .65), fill=theme.on_surface_variant, width=2)
                x += length
        for line in title_lines:
            paint(line, 80, y, 42); y += 56
        current_count = sum(not item.get("historical") for item in sources.values())
        draw.text((80, y + 3), f"本期 {current_count} 条 · {at.astimezone(ZoneInfo('Asia/Shanghai')):%Y-%m-%d %H:%M} · 第 {index + 1}/{len(pages)} 页", font=load_font(22), fill=theme.on_surface_variant)
        y += 58
        for block, cells, block_height in units:
            if block.kind == "rule":
                draw.line((80, y + 10, width - 80, y + 10), fill=theme.outline, width=2)
            elif cells is not None:
                column = content_width / len(cells)
                for col, lines in enumerate(cells):
                    x = 80 + col * column
                    draw.rectangle((x, y, x + column, y + block_height), fill=theme.container_high, outline=theme.outline)
                    for row_index, line in enumerate(lines):
                        paint(line, x + 14, y + 8 + row_index * 34, block.size)
            elif block.kind not in {"space"}:
                if block.kind in {"quote", "timeline"}:
                    draw.line((86, y, 86, y + block_height), fill=theme.primary, width=3)
                if block.kind in {"code", "table"}:
                    draw.rectangle((80, y, width - 80, y + block_height), fill=theme.container_high)
                paint(block.spans, 94 + block.indent, y, block.size)
            y += block_height
        draw.text((80, image.height - 88), "大模型基于已送达新闻与同一通知对象的历史记忆整理；关联不代表因果。", font=load_font(20), fill=theme.on_surface_variant)
        output = BytesIO(); image.save(output, format="PNG", optimize=True); result.append(output.getvalue())
    return result
