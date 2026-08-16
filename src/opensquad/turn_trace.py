"""Turn identity + cancelled-history sealing (no extra observability stack).

trace_id = ``{agent_id}:{sid}:{round_id}:{turn_id}`` so agent.log and
session events can be grepped to the same LLM hop.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

CANCELLED_TOOL_RESULT = "Cancelled: stopped by user"
_TURN_SUMMARY_MAX = 50


def make_trace_id(agent_id: str | None, sid: str | None, round_id: int | None, turn_id: int | None) -> str:
    return f"{agent_id or '-'}:{sid or '-'}:{int(round_id or 0)}:{int(turn_id or 0)}"


def has_unclosed_tool_call(text: str | None) -> bool:
    if not text or "<tool_call" not in text:
        return False
    return "</tool_call>" not in text


def _assistant_tool_call_ids(msg: dict) -> list[str]:
    ids: list[str] = []
    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        cid = str(tc.get("id") or "").strip()
        if cid:
            ids.append(cid)
    return ids


def seal_cancelled_history(chat_api: Any, *, cancel_text: str = CANCELLED_TOOL_RESULT) -> dict[str, Any]:
    """Make the next LLM turn valid after a user stop.

    - Drop a trailing assistant message that still has an unclosed ``<tool_call>``.
    - For native-FC tool_calls already on the last assistant, append matching
      ``role=tool`` cancelled results so ids are not orphaned.
    """
    open_tool_ids: list[str] = []
    partial_persisted = False
    req = getattr(chat_api, "req", None)
    if not isinstance(req, list) or len(req) < 2:
        return {"open_tool_ids": open_tool_ids, "partial_persisted": partial_persisted}

    last = None
    last_idx = -1
    for i in range(len(req) - 1, 0, -1):
        msg = req[i]
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "assistant":
            last = msg
            last_idx = i
            break
        if role != "tool":
            break
    if last is None:
        return {"open_tool_ids": open_tool_ids, "partial_persisted": partial_persisted}

    content = last.get("content") or ""
    # Only pop truncated XML when it is still the trailing message (no tool
    # results have been committed after it).
    if last_idx == len(req) - 1 and isinstance(content, str) and has_unclosed_tool_call(content):
        pop = getattr(chat_api, "pop_last_assistant_message", None)
        if callable(pop):
            pop()
        return {"open_tool_ids": open_tool_ids, "partial_persisted": False}

    if isinstance(content, str) and content.strip():
        partial_persisted = True

    answered: set[str] = set()
    for msg in req:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool":
            cid = str(msg.get("tool_call_id") or "").strip()
            if cid:
                answered.add(cid)

    for cid in _assistant_tool_call_ids(last):
        if cid not in answered:
            open_tool_ids.append(cid)

    add_result = getattr(chat_api, "add_tool_result", None)
    if callable(add_result):
        for tc in last.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            cid = str(tc.get("id") or "").strip()
            if not cid or cid in answered:
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str(fn.get("name") or tc.get("name") or "cancelled")
            raw_args = fn.get("arguments") if fn else tc.get("arguments")
            args: dict = {}
            if isinstance(raw_args, dict):
                args = raw_args
            elif isinstance(raw_args, str) and raw_args.strip():
                try:
                    parsed = json.loads(raw_args)
                    if isinstance(parsed, dict):
                        args = parsed
                except Exception:
                    args = {}
            try:
                add_result(tool_name=name, tool_args=args, result=cancel_text, tool_call_id=cid)
            except Exception:
                logger.debug("[turn_trace] add_tool_result on cancel failed id=%s", cid, exc_info=True)

    return {"open_tool_ids": open_tool_ids, "partial_persisted": partial_persisted}


def append_turn_summary_file(agent_dir: str, payload: dict) -> None:
    """Keep last N turn summaries next to session history for crash forensics."""
    if not agent_dir:
        return
    path = os.path.join(agent_dir, "data", "ai_his_talk", "turn_summaries.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows: list = []
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                rows = loaded
        except Exception:
            rows = []
    rows.append(payload)
    rows = rows[-_TURN_SUMMARY_MAX:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
