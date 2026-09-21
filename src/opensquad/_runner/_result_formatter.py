"""
Result formatting module — functions for formatting tool execution results.

Extracted from runner.py to reduce its size.

Note on liveness: ``truncate_result_text`` / ``summarize_result`` below are the
*shadow* copies of what ``_runner/_tool_executor`` still binds onto the runner.
The helpers at the bottom of this file (``is_failure_result``,
``format_result_for_llm`` and ``failure_key``) are different — they are the live
path, imported by ``_runner/_turn_loop``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def truncate_result_text(text: str, max_len: int | None) -> str:
    """Truncate text to max_len chars, preserving head and tail portions.
    If max_len is None or <= 0, no truncation is applied.
    """
    if max_len is None or max_len <= 0 or len(text) <= max_len:
        return text
    if max_len >= 50000:
        return text[:25000] + "...[truncated]..." + text[-10000:]
    else:
        return text[:1000] + "...[truncated]..." + text[-500:]


def get_tool_output_max_chars(config_path: str) -> int:
    """Read tool_output_max_chars from agent config.json.
    Returns 0 for no limit; defaults to 50000 chars if not configured.
    """
    from opensquad.json_cache import load_json_cached

    try:
        if config_path and os.path.isfile(config_path):
            cfg = load_json_cached(config_path, default={})
            val = cfg.get("model", {}).get("tool_output_max_chars")
            if val is not None:
                v = int(val)
                if v < 0:
                    return 0
                return v
    except (ValueError, TypeError, AttributeError):
        pass
    return 50000


def summarize_result(name: str, result: Any, config_path: str = "") -> str:
    """Format a tool execution result into a string suitable for LLM context.

    Args:
        name: Tool name.
        result: Tool result (string, dict, or any).
        config_path: Path to agent config.json (used to read tool_output_max_chars).

    Returns:
        Formatted result string with timestamp.
    """
    now = datetime.now().strftime("%H:%M:%S")

    # MCP multimodal result (contains screenshots): keep only the text portion
    if isinstance(result, dict) and result.get("__mcp_multimodal__"):
        text = result.get("text", "")
        img_count = len(result.get("images", []))
        res_str = f"{text} [+{img_count} screenshot(s) attached]"
    else:
        res_str = str(result)

    # Skill-related reads should preserve content to avoid cutting SKILL.md
    tool_name = (name or "").lower()
    is_skill_read = "read_skill" in tool_name or "activate_skill" in tool_name
    is_skill_related = is_skill_read or "skill" in tool_name

    if is_skill_read:
        max_len = None
    elif is_skill_related:
        max_len = 50000
    else:
        max_len = get_tool_output_max_chars(config_path)
        max_len = max_len if max_len > 0 else None

    res_str = truncate_result_text(res_str, max_len)
    return f"[{now}] Tool '{name}' executed. Result: {res_str}"


# ---------------------------------------------------------------------------
# Live helpers (the runtime path, used by _runner/_turn_loop.py)
# ---------------------------------------------------------------------------

# Keys carrying the *diagnostic* payload of a tool result whose human-readable
# summary lives in `message`.  The turn loop used to collapse any result dict
# that had a `message` key down to that one string, so a shell that died
# mid-command told the model "Command aborted (shell closed or process exited)"
# while the captured output, the exit code and the session id were thrown away.
# The model could not tell "retry" from "switch strategy" and looped 48 rounds
# (session 20260921_084718_9l88).  Order matters: the trailing entries are the
# ones `truncate_result_text`'s tail window keeps.
_RESULT_DETAIL_KEYS = (
    "reason",
    "session_id",
    "working_directory",
    "return_code",
    "exit_code",
    "partial_data",
    "output",
    "stdout",
    "stderr",
    "error",
    "detail",
    "hint",
)

# `status` values that mean the call did not do its job.
_FAILURE_STATUSES = frozenset({"error", "failed", "failure"})


def is_failure_result(result: Any) -> bool:
    """True when a tool result represents a failure to execute / make progress.

    ``completed=False`` is deliberately NOT a failure: "still running" is a
    legitimate poll result, and counting it would flag long builds.
    """
    if isinstance(result, str):
        return result.startswith("Error:")
    if not isinstance(result, dict):
        return False
    if result.get("aborted") or result.get("timed_out"):
        return True
    status = result.get("status")
    return isinstance(status, str) and status.strip().lower() in _FAILURE_STATUSES


def format_result_for_llm(result: Any) -> str:
    """Render a tool result as the text the model actually receives.

    A dict whose ``message`` reads as a human summary stays headline-first, but
    the diagnostic siblings listed in ``_RESULT_DETAIL_KEYS`` are appended
    instead of being dropped.  Anything else keeps the historical
    ``str(result)`` rendering.
    """
    if not isinstance(result, dict):
        return str(result) if result else "(empty result)"
    message = result.get("message")
    if not (isinstance(message, str) and message.strip()):
        return str(result) if result else "(empty result)"
    details: list[str] = []
    for key in _RESULT_DETAIL_KEYS:
        if key not in result:
            continue
        value = result[key]
        if value is None:
            continue
        details.append(f"[{key}] {str(value).strip() or '(empty)'}")
    if not details:
        return message
    return message + "\n" + "\n".join(details)


# ---------------------------------------------------------------------------
# failure_key — the stable identity of a failure, for the repeat guard
# ---------------------------------------------------------------------------

# Identifiers of the *attempt*, not properties of the *failure*.  A loop that
# retries under a fresh shell changes exactly these, so anything hashing them
# sees a brand-new problem every round.
_VOLATILE_KEYS = frozenset({"session_id", "call_id", "tool_call_id"})

# Numbers are volatile across retries of one and the same problem: exit code,
# pid, port, line number, date.  "exit code 1" and "exit code 2" are the same
# complaint; masking them keeps one streak without merging genuinely different
# failures (those differ in words, not in digits).
_DIGITS_RE = re.compile(r"\d+")
_WHITESPACE_RE = re.compile(r"\s+")


def _mask_volatile(text: str) -> str:
    """Replace runs of digits with ``#`` and collapse whitespace."""
    return _WHITESPACE_RE.sub(" ", _DIGITS_RE.sub("#", text)).strip()


def failure_key(result: Any) -> str:
    """Stable identity of *what went wrong*, independent of the attempt.

    This is the FAILURE signal of the repeated-action guard.  Two aborts that
    differ only in the shell that produced them MUST fingerprint identically:
    the model in session ``20260921_084718_9l88`` minted a fresh ``session_id``
    every round (``chk`` → ``chk24``), and because the rendered result text
    carries ``session_id`` (see ``_RESULT_DETAIL_KEYS``) a guard that digested
    that text reset its counter every round — it never fired once in 40 rounds
    on the very payload it was written for.

    So the identity is *our own taxonomy* and never the identifiers of the
    attempt.  Precedence:

    1. ``reason`` — our machine-readable failure kind.  When a tool sets it, that
       *is* the identity; the prose beside it is derived (and frequently echoes
       the command), so letting it in would re-import the volatility.
    2. ``status`` + the message with digits masked — for tools that report only a
       status, which on its own is too coarse to be an identity.
    3. a rendering of the remaining fields with the volatile ids removed.
    """
    if isinstance(result, dict):
        kind = result.get("reason")
        if kind is not None and str(kind).strip():
            return str(kind).strip()
        parts: list[str] = []
        status = result.get("status")
        if status is not None and str(status).strip():
            parts.append(str(status).strip())
        message = result.get("message")
        if isinstance(message, str) and message.strip():
            parts.append(_mask_volatile(message))
        if parts:
            return " | ".join(parts)
        rest = {k: v for k, v in result.items() if k not in _VOLATILE_KEYS}
        return _mask_volatile(json.dumps(rest, sort_keys=True, ensure_ascii=False, default=str))
    return _mask_volatile(str(result))
