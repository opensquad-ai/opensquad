"""
Compression module -- summary payload building + external summarizer LLM call.

Extracted from runner.py to reduce its size.

Upgrades over the original runner.py copy:

- ``estimate_tokens`` / ``estimate_context_tokens`` -- token estimation with
  tiktoken when available (falls back to the char/2 heuristic used by
  session_manager so behaviour stays consistent in minimal runtimes).
- ``extract_file_operations`` -- tracks which files were read vs modified from
  tool_call / tool_result events, so the summarizer can preserve file state
  across compactions (mirrors pi's CompactionDetails.readFiles/modifiedFiles).
- Budget-based truncation -- long items keep head + tail instead of a
  head-only cut, so key parameters at the end of a tool result survive.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from opensquad.system_config import syscfg
from opensquad.tool import logger

__all__ = [
    "build_summary_payload",
    "estimate_context_tokens",
    "estimate_tokens",
    "extract_file_operations",
    "run_external_summarizer",
]

# ── Token estimation ──────────────────────────────────────────────────────

_TOKENIZER: Any = None
_TOKENIZER_FAILED = False


def _get_tokenizer():
    """Lazily load tiktoken cl100k; returns None when unavailable/broken."""
    global _TOKENIZER, _TOKENIZER_FAILED
    if _TOKENIZER_FAILED:
        return None
    if _TOKENIZER is None:
        try:
            import tiktoken

            _TOKENIZER = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _TOKENIZER_FAILED = True
            _TOKENIZER = None
    return _TOKENIZER


def estimate_tokens(text: Any) -> int:
    """Estimate token count. tiktoken when present, else char/2 heuristic."""
    if not text:
        return 0
    enc = _get_tokenizer()
    if enc is not None:
        try:
            return len(enc.encode(str(text)))
        except Exception:
            pass
    return max(1, len(str(text)) // 2)


def estimate_context_tokens(messages: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, int]:
    """Estimate tokens for messages + events, plus per-category breakdown.

    Uses the same serialization shape as the payload builder so the numbers
    reported to the summarizer match what it actually receives.
    """
    msg_tokens = sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
    evt_tokens = 0
    for evt in events:
        data = evt.get("data", evt.get("content", ""))
        text = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else str(data or "")
        evt_tokens += estimate_tokens(text)
    return {
        "messages": msg_tokens,
        "events": evt_tokens,
        "total": msg_tokens + evt_tokens,
        "message_count": len(messages),
        "event_count": len(events),
    }


# ── File-operation tracking ───────────────────────────────────────────────

# Tool functions that touch project files. Matched by bare function name
# (events carry the name the model called, e.g. ``read_file``); MCP tools use
# ``mcp__<server>__read_file`` so a suffix match keeps them covered.
_FILE_TOOL_READ = frozenset(
    {
        "read_file",
        "list_directory",
        "search_files",
        "find_files",
        "cat",
        "ls",
        "grep",
        "glob",
    }
)

_FILE_TOOL_MODIFY = frozenset(
    {
        "write_file",
        "replace_in_file",
        "delete_file",
        "create_directory",
        "move_file",
        "rename_file",
        "copy_file",
        "edit_file",
        "append_file",
    }
)

# Common arg keys that carry file paths in tool schemas.
_PATH_ARG_KEYS = (
    "path",
    "file_path",
    "filename",
    "file",
    "src",
    "dst",
    "target",
    "dir",
    "directory",
    "workspace",
)


def _event_tool_data(evt: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Extract (tool_name, args_dict) from a tool_call/tool_result event."""
    data = evt.get("data", evt.get("content", ""))
    if isinstance(data, str):
        with contextlib.suppress(Exception):
            data = json.loads(data)
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if not name:
        return None
    args = data.get("args")
    if isinstance(args, str):
        with contextlib.suppress(Exception):
            args = json.loads(args)
    if not isinstance(args, dict):
        args = {}
    return str(name), args


def _is_file_tool(name: str, reads: frozenset[str], modifies: frozenset[str]) -> str | None:
    """Classify a tool name as 'read' or 'modify'; None when not file-related.

    Explicit sets win over suffix heuristics: ``write_file`` ends with
    ``_file`` but is a modify, so the modify set must be checked first.
    """
    if name in modifies or ("__" in name and name.rsplit("__", 1)[-1] in modifies):
        return "modify"
    if name in reads or ("__" in name and name.rsplit("__", 1)[-1] in reads):
        return "read"
    if name.endswith("_file") or name.endswith("_directory"):
        return "read"
    return None


def extract_file_operations(
    messages: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    changeset_files: list[dict[str, Any]] | None = None,
) -> dict[str, list[str]]:
    """Collect read / modified paths from tool events.

    ``changeset_files`` may be the session changeset ``summary()["files"]``
    list -- those are always treated as modified (the changeset only records
    files the agent actually mutated). Paths are deduplicated case-insensitively
    (Windows-friendly) and sorted for deterministic payloads.
    """
    read: dict[str, str] = {}
    modified: dict[str, str] = {}

    def _add(bucket: dict[str, str], path: Any) -> None:
        if not path or not isinstance(path, str):
            return
        stripped = path.strip()
        if not stripped:
            return
        key = stripped.casefold()
        bucket.setdefault(key, stripped)

    for evt in events:
        parsed = _event_tool_data(evt)
        if parsed is None:
            continue
        name, args = parsed
        kind = _is_file_tool(name, _FILE_TOOL_READ, _FILE_TOOL_MODIFY)
        if kind is None:
            continue
        bucket = modified if kind == "modify" else read
        for key in _PATH_ARG_KEYS:
            _add(bucket, args.get(key))

    for f in changeset_files or []:
        if isinstance(f, dict) and f.get("path"):
            _add(modified, f["path"])

    return {
        "read": [read[k] for k in sorted(read)],
        "modified": [modified[k] for k in sorted(modified)],
    }


# ── Payload building ──────────────────────────────────────────────────────

# Per-item truncation budget (chars). Long tool results keep head + tail so
# closing parameters / final status lines survive compression.
_ITEM_BUDGET_CHARS = 1200
_HEAD_RATIO = 0.6


def _truncate_item(text: str, budget: int = _ITEM_BUDGET_CHARS) -> str:
    """Truncate to budget chars, keeping head + tail when cut is needed."""
    text = text.strip()
    if len(text) <= budget:
        return text
    head_len = int(budget * _HEAD_RATIO)
    tail_len = budget - head_len
    if tail_len < 40:
        return text[: budget - 1] + "…"
    return f"{text[:head_len]} …[{len(text) - head_len - tail_len} chars omitted]… {text[-tail_len:]}"


def _event_text(evt: dict[str, Any]) -> str:
    data = evt.get("data", evt.get("content", ""))
    return json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else str(data or "")


def build_summary_payload(
    previous_summary: str,
    messages: list[dict[str, Any]],
    events: list[dict[str, Any]],
    keep_last: int | None = None,
    *,
    file_ops: dict[str, list[str]] | None = None,
    token_stats: dict[str, int] | None = None,
) -> str:
    """Build the text payload sent to the summarizer LLM.

    Signature stays backward compatible with the original runner.py copy;
    ``file_ops`` / ``token_stats`` are optional and computed on demand when
    omitted (kept separate so callers can reuse one estimation across calls).
    """
    lines: list[str] = []

    if previous_summary and previous_summary.strip():
        lines.append("[Previous Context Summary]")
        lines.append(previous_summary.strip())
    else:
        lines.append("[Previous Context Summary]\n(none)")

    # Context stats -- lets the summarizer gauge how much was compressed.
    if token_stats is None:
        token_stats = estimate_context_tokens(messages, events)
    lines.append("\n[Context Stats]")
    lines.append(
        f"Estimated tokens: messages={token_stats.get('messages', 0)}, "
        f"events={token_stats.get('events', 0)}, total={token_stats.get('total', 0)} "
        f"({token_stats.get('message_count', len(messages))} messages, "
        f"{token_stats.get('event_count', len(events))} events)."
    )

    # Files touched -- preserved across compactions so the next context knows
    # exactly which files were read and which were modified.
    if file_ops is None:
        file_ops = extract_file_operations(messages, events)
    lines.append("\n[Files Touched]")
    modified = file_ops.get("modified") or []
    read = file_ops.get("read") or []
    if modified:
        lines.append("Modified (preserve exact paths, describe what changed in each):")
        lines.extend(f"- {p}" for p in modified)
    else:
        lines.append("Modified: (none)")
    if read:
        lines.append("Read (paths referenced by the conversation):")
        lines.extend(f"- {p}" for p in read)
    else:
        lines.append("Read: (none)")

    lines.append("\n[Conversation Messages to Compress]")
    msgs_to_compress = messages[:-keep_last] if keep_last is not None and len(messages) > keep_last else messages

    for msg in msgs_to_compress:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content:
            continue
        short = _truncate_item(str(content))
        lines.append(f"- {role}: {short}")

    lines.append("\n[Workflow Events to Compress]")
    for evt in events:
        etype = evt.get("type", "")
        text = _event_text(evt).strip()
        if not text:
            continue
        lines.append(f"- {etype}: {_truncate_item(text)}")

    lines.append("\n[Compression Rules]")
    lines.append(
        "Use the exact summary template with these sections: "
        "Current Task, Original Goal, Completed, Current State, "
        "Key Parameters, Unresolved Issues."
    )
    lines.append(
        "Current Task MUST describe what the agent is working on RIGHT NOW -- the most recent user request in detail."
    )
    lines.append("Original Goal is the very first user request in this session, in one sentence.")
    lines.append(
        "Current State is the most important section -- include open files, current directory, last tool executed."
    )
    lines.append("Preserve all file paths, IDs, ports, version numbers, config values, and error messages verbatim.")
    lines.append(
        "For every file listed under Modified in [Files Touched], state exactly what changed (functions, config keys, data)."
    )
    lines.append(
        "You MUST consider full workflow context: thought, plan, tool_call, tool_result, and info/status events."
    )
    lines.append("Completed must include Done/In progress/Todo sub-bullets with specific file paths.")
    if keep_last is not None:
        lines.append(
            f"Keep only the last {keep_last} messages in live chat history; "
            "everything above must be summarized into CONTEXT_SUMMARY."
        )
    else:
        lines.append(
            "Compress ALL messages and events into CONTEXT_SUMMARY. "
            "Keep only the newest 10% of content as live context."
        )
    return "\n".join(lines)


async def run_external_summarizer(
    summary_payload: str,
    base_url: str,
    api_key: str,
    model: str,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Call an external LLM to generate a context summary."""
    model = syscfg.get("summarizer", "model") or model or "gpt-4o-mini"

    system_prompt = (
        "You are a summarizer agent. Return ONLY the summary in the "
        "specified template. Do not add commentary or extra sections."
    )

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=60)
        logger.info(
            "[Compression] Calling external summarizer: model=%s, payload_len=%d",
            model,
            len(summary_payload),
        )

        if on_chunk is None:
            response = await client.chat.completions.create(
                model=model,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": summary_payload},
                ],
            )
            content = response.choices[0].message.content or ""
            return content.strip()

        stream = await client.chat.completions.create(
            model=model,
            temperature=0.2,
            stream=True,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": summary_payload},
            ],
        )

        parts: list[str] = []
        async for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content or ""
            except Exception:
                delta = ""
            if not delta:
                continue
            parts.append(delta)
            with contextlib.suppress(Exception):
                await on_chunk(delta)

        result = "".join(parts).strip()
        if not result:
            logger.warning(
                "[Compression] External summarizer returned empty result (model=%s, payload_len=%d)",
                model,
                len(summary_payload),
            )
        return result

    except Exception as e:
        logger.error(f"[Compression] External summarizer failed: {e}")
        return ""
