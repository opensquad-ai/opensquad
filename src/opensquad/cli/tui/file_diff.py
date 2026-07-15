"""OpenCode-style unified file-edit diffs for the TUI.

Aligns with Web FileDiffBlock recognition:
- replace_in_file / edit_file → old/new hunks
- write_file → treat content as all-added
"""

from __future__ import annotations

import difflib
import json
from typing import Any

_MAX_DIFF_LINES = 80  # hard cap rendered lines (before truncation markers)
_MAX_WRITE_PREVIEW = 40  # lines for write_file content preview


def _norm_tool_name(name: str) -> str:
    n = str(name or "").strip().lower()
    return n.replace(".", "__")


def is_file_edit_tool(name: str, args: dict[str, Any] | None = None) -> bool:
    """True for filesystem replace/write (and MCP edit_file aliases)."""
    n = _norm_tool_name(name)
    args = args if isinstance(args, dict) else {}
    if any(k in n for k in ("replace_in_file", "edit_file", "str_replace")):
        return True
    if any(k in n for k in ("write_file", "create_file")):
        return True
    if args.get("old_str") is not None or args.get("old_string") is not None or args.get("oldString") is not None:
        return True
    return "write" in n and args.get("content") is not None


def is_file_write_tool(name: str) -> bool:
    n = _norm_tool_name(name)
    return any(k in n for k in ("write_file", "create_file")) and "replace" not in n


def parse_tool_args(raw: Any) -> dict[str, Any]:
    """Normalize tool args from dict or JSON string."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def extract_edit_fields(args: dict[str, Any]) -> tuple[str, str, str, str]:
    """Return (path, old_str, new_str, content)."""
    path = str(
        args.get("path")
        or args.get("file_path")
        or args.get("filePath")
        or args.get("filename")
        or args.get("file")
        or ""
    ).strip()
    old_str = args.get("old_str")
    if old_str is None:
        old_str = args.get("old_string")
    if old_str is None:
        old_str = args.get("oldString")
    if old_str is None:
        old_str = args.get("oldStr")
    new_str = args.get("new_str")
    if new_str is None:
        new_str = args.get("new_string")
    if new_str is None:
        new_str = args.get("newString")
    if new_str is None:
        new_str = args.get("newStr")
    content = args.get("content")
    if content is None:
        content = args.get("text")
    return (
        path,
        "" if old_str is None else str(old_str),
        "" if new_str is None else str(new_str),
        "" if content is None else str(content),
    )


def _escape(s: str) -> str:
    return s.replace("[", "\\[")


def build_unified_hunks(
    old: str,
    new: str,
    *,
    start_line: int = 1,
) -> list[tuple[str, int | None, int | None, str]]:
    """
    Build unified diff rows as (kind, old_ln, new_ln, text).

    kind: ' ' context | '-' remove | '+' add
    """
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    # Keep trailing empty line visible if present
    if old.endswith("\n"):
        old_lines.append("")
    if new.endswith("\n"):
        new_lines.append("")

    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    rows: list[tuple[str, int | None, int | None, str]] = []
    o_base = max(1, int(start_line or 1))
    # SequenceMatcher indexes are 0-based within the provided slices;
    # map to absolute line numbers via o_base / n_base.
    n_base = o_base

    # Track absolute line counters as we walk opcodes
    o_cur = o_base
    n_cur = n_base
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k, line in enumerate(old_lines[i1:i2]):
                rows.append((" ", o_cur + k, n_cur + k, line))
            o_cur += i2 - i1
            n_cur += j2 - j1
        elif tag == "delete":
            for k, line in enumerate(old_lines[i1:i2]):
                rows.append(("-", o_cur + k, None, line))
            o_cur += i2 - i1
        elif tag == "insert":
            for k, line in enumerate(new_lines[j1:j2]):
                rows.append(("+", None, n_cur + k, line))
            n_cur += j2 - j1
        elif tag == "replace":
            for k, line in enumerate(old_lines[i1:i2]):
                rows.append(("-", o_cur + k, None, line))
            o_cur += i2 - i1
            for k, line in enumerate(new_lines[j1:j2]):
                rows.append(("+", None, n_cur + k, line))
            n_cur += j2 - j1
    return rows


def count_plus_minus(rows: list[tuple[str, int | None, int | None, str]]) -> tuple[int, int]:
    plus = sum(1 for k, *_ in rows if k == "+")
    minus = sum(1 for k, *_ in rows if k == "-")
    return plus, minus


def format_opencode_diff_markup(
    *,
    path: str,
    op: str,
    old: str,
    new: str,
    start_line: int = 1,
    max_lines: int = _MAX_DIFF_LINES,
    red: str = "#f85149",
    green: str = "#3fb950",
    muted: str = "#8b949e",
    fg: str = "#e6edf3",
) -> list[str]:
    """
    Return Rich markup lines for an OpenCode-like file edit block.

    Header: ← Edit path  (+N -M)
    Body:   line │ ± text   with red/green backgrounds
    """
    rows = build_unified_hunks(old, new, start_line=start_line)
    plus, minus = count_plus_minus(rows)
    label = "Write" if op == "write" else "Edit"
    path_s = _escape(path or "(unknown)")
    stats = ""
    if plus or minus:
        stats = f"  [{green}]+{plus}[/] [{red}]-{minus}[/]"
    lines: list[str] = [f"[dim {muted}]← {label}[/] [{fg}]{path_s}[/]{stats}"]

    # Truncate huge diffs: keep head + tail
    if len(rows) > max_lines:
        head_n = max_lines // 2
        tail_n = max_lines - head_n
        omitted = len(rows) - max_lines
        display = rows[:head_n] + [("…", None, None, f"… {omitted} lines …")] + rows[-tail_n:]
    else:
        display = rows

    # Width for line number column
    nums = [n for _, o, n, _ in display for n in (o, n) if isinstance(n, int)]
    width = max(2, len(str(max(nums))) if nums else 2)

    for kind, o_ln, n_ln, text in display:
        if kind == "…":
            lines.append(f"[dim {muted}]  {text}[/]")
            continue
        ln = o_ln if o_ln is not None else n_ln
        ln_s = str(ln if ln is not None else "").rjust(width)
        shown = _escape(text.replace("\t", "  "))
        if kind == "-":
            lines.append(f"[{fg} on #490202]{ln_s} │ - {shown}[/]")
        elif kind == "+":
            lines.append(f"[{fg} on #0a2f1a]{ln_s} │ + {shown}[/]")
        else:
            lines.append(f"[dim {muted}]{ln_s} │   {shown}[/]")
    return lines


def markup_from_tool_payload(
    name: str,
    args: dict[str, Any] | None = None,
    *,
    diff_old: str | None = None,
    diff_new: str | None = None,
    diff_start_line: int | None = None,
) -> list[str] | None:
    """
    Build markup lines from tool name/args and optional server diff_* fields.
    Returns None if this is not a file-edit tool or there is nothing to show.
    """
    args = args if isinstance(args, dict) else {}
    if not is_file_edit_tool(name, args):
        return None

    path, old_str, new_str, content = extract_edit_fields(args)
    start = int(diff_start_line or 1)

    # Prefer server-expanded context when present
    if diff_old is not None and diff_new is not None:
        return format_opencode_diff_markup(
            path=path,
            op="edit",
            old=str(diff_old),
            new=str(diff_new),
            start_line=start,
        )

    if is_file_write_tool(name) or (content and not old_str and "write" in _norm_tool_name(name)):
        # write_file: show content as all-green additions (truncated)
        body = content
        lines = body.splitlines() or [""]
        if len(lines) > _MAX_WRITE_PREVIEW:
            head = "\n".join(lines[: _MAX_WRITE_PREVIEW // 2])
            tail = "\n".join(lines[-(_MAX_WRITE_PREVIEW // 2) :])
            omitted = len(lines) - _MAX_WRITE_PREVIEW
            body = f"{head}\n… {omitted} lines …\n{tail}"
        return format_opencode_diff_markup(
            path=path or "(new file)",
            op="write",
            old="",
            new=body,
            start_line=1,
        )

    if old_str or new_str:
        return format_opencode_diff_markup(
            path=path,
            op="edit",
            old=old_str,
            new=new_str,
            start_line=1,
        )

    if path:
        # Streaming / incomplete args — header only
        label = "Write" if is_file_write_tool(name) else "Edit"
        return [f"[dim #8b949e]← {label}[/] [#e6edf3]{_escape(path)}[/]"]
    return None


def markup_from_event_payload(payload: dict[str, Any], *, phase: str = "call") -> list[str] | None:
    """Build markup from a WS tool_call / tool_result payload dict."""
    name = str(payload.get("name") or payload.get("tool") or "")
    args = parse_tool_args(payload.get("args") or payload.get("arguments") or {})
    diff_old = payload.get("diff_old")
    diff_new = payload.get("diff_new")
    diff_start = payload.get("diff_start_line")
    if phase == "result":
        # Result without server diffs: still allow args-based paint if never shown
        return markup_from_tool_payload(
            name,
            args,
            diff_old=None if diff_old is None else str(diff_old),
            diff_new=None if diff_new is None else str(diff_new),
            diff_start_line=int(diff_start) if diff_start is not None else None,
        )
    return markup_from_tool_payload(name, args)
