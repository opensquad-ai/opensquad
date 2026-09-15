"""Live XML/DSML tool-call preview while the closing tag is still missing.

Commit-on-close parsers used to swallow `<tool_call>` until `</tool_call>`.
Cheap models often hang mid-tag, so Agent Web showed thoughts but no tool row
and later leaked the markup as plain chat text. This module:

- peeks the in-flight commit buffer and emits ``tool_call_delta`` as soon as
  a tool name is recognizable;
- best-effort-parses unclosed blocks so the turn loop can still execute them.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

_TOOL_PREVIEW_TAGS = frozenset(
    {
        "tool_call",
        "tool_calls",
        "function_calls",
        "calls",
        "invoke",
        "func",
        "parameter",
        "arguments",
    }
)


def attach_xml_tool_preview(parser, emit_with_sid: Callable[[str, Any], None]) -> None:
    """Register a per-chunk preview callback on ``StreamingTagParser``."""
    if parser is None or not hasattr(parser, "set_commit_progress_callback"):
        return

    last_sig: list[Any] = [None]
    last_at: list[float] = [0.0]

    def _on_progress(tag: str, buf: str, attrs: dict[str, str] | None) -> None:
        if (tag or "").split(":")[-1].lower() not in _TOOL_PREVIEW_TAGS:
            return
        from opensquad.parser import ResponseParser

        parsed = ResponseParser.parse_partial_tool_preview(tag, buf, attrs)
        if not parsed:
            return
        name, args = parsed
        if not name:
            return
        now = time.monotonic()
        try:
            args_key = json.dumps(args, sort_keys=True, ensure_ascii=False) if isinstance(args, dict) else str(args)
        except (TypeError, ValueError):
            args_key = str(args)
        sig = ("xml_preview_open", name, args_key)
        if sig == last_sig[0] and (now - last_at[0]) < 0.12:
            return
        last_sig[0] = sig
        last_at[0] = now
        payload: dict[str, Any] = {
            "id": "xml_preview_open",
            "index": 0,
            "name": name,
            "arguments": args_key if isinstance(args, dict) else (args or ""),
            "args": args if isinstance(args, dict) else args_key,
            "partial": True,
            "force": True,
        }
        emit_with_sid("tool_call_delta", payload)

    parser.set_commit_progress_callback(_on_progress)
