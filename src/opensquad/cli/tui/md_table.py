"""Parse GFM markdown pipe-tables and render as OpenCode-like grid text."""

from __future__ import annotations

import re
from typing import Any, Iterator

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

# Header | Sep | ≥1 body row (GFM). Models often emit `|-|-|` (1 dash) — accept 1+.
_SEP_CELL = re.compile(r"^\s*:?-+:?\s*$")
_PIPE_LINE = re.compile(r"^\s*\|.*\|\s*$")


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_separator(line: str) -> bool:
    raw = line.strip()
    if "|" not in raw and not re.match(r"^[\s:\-]+$", raw):
        return False
    if "|" not in raw:
        cells = [c for c in re.split(r"\s+", raw) if c]
    else:
        cells = _split_row(raw)
    if not cells:
        return False
    ok = all(_SEP_CELL.match(c) or c == "" for c in cells)
    return ok and any(_SEP_CELL.match(c) for c in cells)


def _alignments(sep_line: str, ncols: int) -> list[str]:
    cells = _split_row(sep_line) if "|" in sep_line else []
    out: list[str] = []
    for i in range(ncols):
        cell = cells[i] if i < len(cells) else "---"
        left = cell.lstrip().startswith(":")
        right = cell.rstrip().endswith(":")
        if left and right:
            out.append("center")
        elif right:
            out.append("right")
        else:
            out.append("left")
    return out


def _strip_inline_md(cell: str) -> str:
    """Light cleanup: unwrap `code` / **bold** / *em* for table cells."""
    s = cell
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", s)
    return s


def find_markdown_tables(text: str) -> list[tuple[int, int, list[str], list[list[str]], list[str]]]:
    """
    Find GFM tables in text.

    Returns list of (start_offset, end_offset, headers, rows, alignments).
    Offsets are character indices into ``text``.
    """
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    pos = 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln)

    found: list[tuple[int, int, list[str], list[list[str]], list[str]]] = []
    i = 0
    n = len(lines)
    while i < n - 1:
        header_raw = lines[i].rstrip("\r\n")
        sep_raw = lines[i + 1].rstrip("\r\n")
        if (
            ("|" in header_raw)
            and _is_separator(sep_raw)
            and (header_raw.strip().startswith("|") or "|" in header_raw.strip())
        ):
            headers = _split_row(header_raw)
            if not headers or all(h == "" for h in headers):
                i += 1
                continue
            aligns = _alignments(sep_raw, len(headers))
            rows: list[list[str]] = []
            j = i + 2
            while j < n:
                row_raw = lines[j].rstrip("\r\n")
                if not row_raw.strip():
                    break
                if "|" not in row_raw:
                    break
                if j + 1 < n and _is_separator(lines[j + 1].rstrip("\r\n")) and not _is_separator(row_raw):
                    if row_raw.strip().startswith("|") and _is_separator(lines[j + 1].rstrip("\r\n")):
                        break
                cells = _split_row(row_raw)
                if len(cells) < len(headers):
                    cells = cells + [""] * (len(headers) - len(cells))
                elif len(cells) > len(headers):
                    cells = cells[: len(headers)]
                rows.append(cells)
                j += 1
            if rows:
                end = offsets[j - 1] + len(lines[j - 1])
                found.append((offsets[i], end, headers, rows, aligns))
                i = j
                continue
        i += 1
    return found


def build_rich_table(
    headers: list[str],
    rows: list[list[str]],
    alignments: list[str] | None = None,
    *,
    border: str = "#8b949e",
    header_style: str = "bold #d2a8ff",
    cell_style: str = "#e6edf3",
    title: str | None = None,
) -> Table:
    """Build a Rich Table with full grid (OpenCode-like)."""
    aligns = alignments or ["left"] * len(headers)
    table = Table(
        title=title,
        box=box.SQUARE,
        border_style=border,
        header_style=header_style,
        show_header=True,
        pad_edge=True,
        expand=False,
        show_lines=True,
    )
    for idx, h in enumerate(headers):
        justify = aligns[idx] if idx < len(aligns) else "left"
        table.add_column(
            _strip_inline_md(h),
            justify=justify,  # type: ignore[arg-type]
            overflow="fold",
            no_wrap=False,
        )
    for row in rows:
        table.add_row(*[Text(_strip_inline_md(c), style=cell_style) for c in row])
    return table


def render_table_markup(
    headers: list[str],
    rows: list[list[str]],
    alignments: list[str] | None = None,
    *,
    border: str = "#8b949e",
    header_style: str = "bold #d2a8ff",
    cell_style: str = "#e6edf3",
    width: int = 100,
) -> Text:
    """
    Render table to a Rich Text with box-drawing borders (safe for RichLog).

    Textual RichLog uses Text.from_markup for strings, which ignores ANSI and can
    drop borders. Returning Text.from_ansi keeps the grid + header colors.
    """
    from io import StringIO

    table = build_rich_table(
        headers,
        rows,
        alignments,
        border=border,
        header_style=header_style,
        cell_style=cell_style,
    )
    # file=StringIO so we do NOT leak a second copy to process stdout
    console = Console(
        file=StringIO(),
        force_terminal=True,
        color_system="truecolor",
        width=max(40, int(width or 100)),
        record=True,
        highlight=False,
    )
    console.print(table)
    ansi = console.export_text(styles=True)
    return Text.from_ansi(ansi.rstrip("\n") + "\n")


def iter_text_and_tables(
    text: str,
    *,
    border: str = "#8b949e",
    header_style: str = "bold #d2a8ff",
    cell_style: str = "#e6edf3",
    width: int = 100,
) -> Iterator[tuple[str, Any]]:
    """
    Yield (\"text\", str) and (\"table\", Text) segments in order.

    Tables are pre-rendered box grids as Text.from_ansi for RichLog safety.
    """
    if not text:
        return
    tables = find_markdown_tables(text)
    if not tables:
        yield ("text", text)
        return

    cursor = 0
    for start, end, headers, rows, aligns in tables:
        if start > cursor:
            chunk = text[cursor:start]
            if chunk.strip():
                yield ("text", chunk.rstrip("\n"))
            elif chunk:
                yield ("text", "")
        yield (
            "table",
            render_table_markup(
                headers,
                rows,
                aligns,
                border=border,
                header_style=header_style,
                cell_style=cell_style,
                width=width,
            ),
        )
        cursor = end
    if cursor < len(text):
        chunk = text[cursor:]
        if chunk.strip():
            yield ("text", chunk.lstrip("\n") if chunk.startswith("\n") else chunk)


def has_markdown_table(text: str) -> bool:
    return bool(find_markdown_tables(text or ""))
