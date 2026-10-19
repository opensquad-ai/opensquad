"""Token breakdown helpers for context-usage stats.

Counts system / user / thought / tool / tool_defs / response from chat_api.req
in a provider-agnostic way (OpenAI role=tool, Claude tool_result blocks,
Gemini functionResponse blocks).
"""

from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from typing import Any, Callable, Hashable

_THOUGHT_RE = re.compile(r"<(thought|think)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_TOOL_CALL_RE = re.compile(r"<tool_call[^>]*>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
_TOOL_RESULT_RE = re.compile(r"<tool_result[^>]*>(.*?)</tool_result>", re.DOTALL | re.IGNORECASE)
_SUMMARIZE_TOOL_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] Tool ['\"]")


def _shape_id(value: Any) -> tuple[int, int, int, int]:
    """Identity + length + first/last nested ids for cheap cache invalidation."""
    if value is None:
        return (0, 0, 0, 0)
    if isinstance(value, str):
        return (id(value), len(value), 0, 0)
    if isinstance(value, dict):
        nested = value.get("text")
        if not isinstance(nested, str):
            nested = value.get("content")
        nested_id = id(nested) if isinstance(nested, (str, list, dict)) else 0
        nested_len = len(nested) if isinstance(nested, (str, list, dict)) else 0
        return (id(value), len(value), nested_id, nested_len)
    if isinstance(value, list):
        first = value[0] if value else None
        last = value[-1] if value else None
        return (id(value), len(value), _nested_payload_id(first), _nested_payload_id(last))
    return (id(value), 0, 0, 0)


def _nested_payload_id(item: Any) -> int:
    if isinstance(item, dict):
        nested = item.get("text")
        if isinstance(nested, str):
            return id(nested)
        nested = item.get("content")
        if isinstance(nested, (str, list, dict)):
            return id(nested)
        return id(item)
    if item is None or isinstance(item, (int, float, bool)):
        return 0
    return id(item)


def message_token_cache_key(message: dict) -> Hashable:
    """Cheap identity+shape key for per-message token LRU caches.

    Messages are treated as immutable after they enter the request list.
    Using object identity and container shape avoids json.dumps on every recount.
    """
    return (
        id(message),
        message.get("role"),
        message.get("name"),
        message.get("tool_call_id"),
        _shape_id(message.get("content")),
        _shape_id(message.get("tool_calls")),
        _shape_id(message.get("reasoning_content")),
    )


def _as_dict(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    if obj is None:
        return {}
    out = {}
    for key in ("name", "arguments", "description", "parameters", "function"):
        if hasattr(obj, key):
            out[key] = getattr(obj, key)
    return out


def tool_fn_text(fn: Any) -> str:
    """Serialize tool function name+arguments for token counting (never raises)."""
    data = _as_dict(fn)
    # Flat tool_call shapes: name/arguments on the tool_call itself
    if not data.get("name") and not data.get("arguments") and isinstance(fn, dict):
        if "name" in fn or "arguments" in fn:
            data = fn
    name = data.get("name") or ""
    args = data.get("arguments")
    if args is None:
        args_s = ""
    elif isinstance(args, str):
        args_s = args
    else:
        try:
            args_s = json.dumps(args, ensure_ascii=False)
        except Exception:
            args_s = str(args)
    return f"{name}{args_s}"


def extract_tool_result_text(item: dict) -> str:
    """Extract plain text from Claude tool_result or Gemini functionResponse blocks."""
    t = item.get("type")
    if t == "tool_result":
        content = item.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    parts.append(c.get("text") or "")
                elif isinstance(c, str):
                    parts.append(c)
            return "\n".join(parts)
        if content is None:
            return ""
        return str(content)
    if t == "functionResponse":
        name = item.get("function_name") or item.get("name") or ""
        resp = item.get("response")
        if isinstance(resp, dict):
            body = resp.get("content", resp.get("result", resp))
        else:
            body = resp
        if body is None:
            body = ""
        elif not isinstance(body, str):
            try:
                body = json.dumps(body, ensure_ascii=False)
            except Exception:
                body = str(body)
        return f"{name}{body}"
    return ""


def is_tool_payload_block(item: Any) -> bool:
    return isinstance(item, dict) and item.get("type") in ("tool_result", "functionResponse", "tool_use")


def content_list_tool_text(items: list) -> str:
    """Concatenate tool-related text from a multimodal content list."""
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t in ("tool_result", "functionResponse"):
            parts.append(extract_tool_result_text(item))
        elif t == "tool_use":
            parts.append(item.get("name", "") + str(item.get("input", {})))
        elif t == "text" and any(is_tool_payload_block(x) for x in items):
            # Ancillary text sitting beside tool_result blocks counts as tool IO
            parts.append(item.get("text") or "")
    return "\n".join(p for p in parts if p)


def make_count_str(encoding: Any | None) -> Callable[[str | None], int]:
    def _count_str(text: str | None) -> int:
        if text is None:
            return 0
        if encoding is not None:
            try:
                return len(encoding.encode(text))
            except Exception:
                pass
        return max(0, len(text) // 4)

    return _count_str


def synthesize_tool_messages_from_events(events: list | None) -> list[dict]:
    """Build OpenAI-style assistant(tool_calls)+tool messages from session events.

    Session persistence often keeps only user/assistant text in ``messages`` and
    stores tool_call / tool_result payloads in ``events``. Token breakdown needs
    those events reconstructed as countable chat messages.
    """
    calls: dict[str, dict[str, str]] = {}
    results: dict[str, str] = {}
    order: list[str] = []

    for e in events or []:
        if not isinstance(e, dict):
            continue
        et = e.get("type")
        data = e.get("data") if isinstance(e.get("data"), dict) else {}
        if not data:
            # Some emitters put fields on the event itself
            data = e
        cid = str(data.get("id") or data.get("call_id") or data.get("tool_call_id") or "").strip()
        if not cid:
            continue
        if et == "tool_call":
            if cid not in calls:
                order.append(cid)
            args = data.get("args")
            if args is None:
                args = data.get("arguments")
            if args is None:
                args = ""
            if not isinstance(args, str):
                try:
                    args = json.dumps(args, ensure_ascii=False)
                except Exception:
                    args = str(args)
            calls[cid] = {
                "name": str(data.get("name") or ""),
                "args": args,
            }
        elif et == "tool_result":
            result = data.get("result")
            if result is None:
                result = data.get("content")
            if result is None:
                result = ""
            if not isinstance(result, str):
                try:
                    result = json.dumps(result, ensure_ascii=False)
                except Exception:
                    result = str(result)
            results[cid] = result
            if cid not in calls:
                order.append(cid)
                args = data.get("args")
                if args is None:
                    args = data.get("arguments") or ""
                if not isinstance(args, str):
                    try:
                        args = json.dumps(args, ensure_ascii=False)
                    except Exception:
                        args = str(args)
                calls[cid] = {
                    "name": str(data.get("name") or ""),
                    "args": args,
                }

    out: list[dict] = []
    for cid in order:
        info = calls.get(cid) or {}
        out.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": cid,
                        "type": "function",
                        "function": {
                            "name": info.get("name") or "",
                            "arguments": info.get("args") or "",
                        },
                    }
                ],
            }
        )
        if cid in results:
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": cid,
                    "content": results[cid],
                }
            )
    return out


def req_has_tool_io(messages: list | None) -> bool:
    """True when messages already carry native tool_calls / role=tool IO."""
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "tool":
            return True
        if m.get("tool_calls"):
            return True
        content = m.get("content")
        if isinstance(content, str) and ("<tool_call" in content or "<tool_result" in content):
            return True
    return False


def enrich_req_with_session_tool_events(
    messages: list | None,
    events: list | None,
) -> list[dict]:
    """Return messages, appending synthesized tool IO from events when missing."""
    req = [m for m in (messages or []) if isinstance(m, dict)]
    if req_has_tool_io(req):
        return req
    synth = synthesize_tool_messages_from_events(events)
    if not synth:
        return req
    return [*req, *synth]


def _accumulate_content_list(
    items: list,
    target: str,
    stats: dict[str, int],
    count_str: Callable[[str | None], int],
) -> None:
    """Fold one message's content-block list into ``stats`` (message-local)."""
    has_tool_payload = any(is_tool_payload_block(x) for x in items)
    for item in items:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "text":
            text = item.get("text", "") or ""
            if has_tool_payload:
                stats["tool"] += count_str(text)
            elif target == "user":
                stats["user"] += count_str(text)
            elif target == "assistant":
                thought_sum = sum(count_str(m.group(2)) for m in _THOUGHT_RE.finditer(text))
                if thought_sum:
                    stats["thought"] += thought_sum
                    text = _THOUGHT_RE.sub("", text).strip()
                stats["response"] += count_str(text)
        elif t in ("tool_result", "functionResponse"):
            stats["tool"] += count_str(extract_tool_result_text(item))
        elif t == "tool_use":
            stats["tool"] += count_str(item.get("name", "") + str(item.get("input", {})))


def _message_stats(message: dict, count_str: Callable[[str | None], int]) -> dict[str, int]:
    """Token contribution of a single message.

    Returns only the message-local buckets — ``tool_defs`` and ``overhead`` are
    request-level and stay in :func:`compute_token_breakdown`. Self-contained by
    construction: every branch depends on this message alone, which is what
    makes the result safely cacheable per message.
    """
    stats: dict[str, int] = {"system": 0, "user": 0, "thought": 0, "tool": 0, "response": 0}
    role = message.get("role")
    content = message.get("content", "")

    # OpenAI / DeepSeek native tool result
    if role == "tool":
        if isinstance(content, str):
            stats["tool"] += count_str(content)
        elif isinstance(content, list):
            stats["tool"] += count_str(content_list_tool_text(content))
        elif content is not None:
            stats["tool"] += count_str(str(content))
        return stats

    if role == "assistant":
        reasoning = message.get("reasoning_content")
        if reasoning:
            stats["thought"] += count_str(reasoning)

        tool_calls = message.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                tc_d = _as_dict(tc)
                fn = tc_d.get("function")
                if fn is None and ("name" in tc_d or "arguments" in tc_d):
                    fn = tc_d
                stats["tool"] += count_str(tool_fn_text(fn))

    counted = False
    if isinstance(content, list):
        target = "user" if role == "user" else "assistant" if role == "assistant" else str(role or "")
        _accumulate_content_list(content, target, stats, count_str)
        counted = True
    elif isinstance(content, str):
        if role == "system":
            stats["system"] += count_str(content)
            counted = True
        elif role == "user":
            if "<tool_result>" in content or "<tool_result " in content:
                tool_sum = sum(count_str(m.group(1)) for m in _TOOL_RESULT_RE.finditer(content))
                stats["tool"] += tool_sum
                stats["user"] += count_str(_TOOL_RESULT_RE.sub("", content).strip())
            elif _SUMMARIZE_TOOL_RE.match(content):
                stats["tool"] += count_str(content)
            else:
                stats["user"] += count_str(content)
            counted = True
        elif role == "assistant":
            thought_sum = sum(count_str(m.group(2)) for m in _THOUGHT_RE.finditer(content))
            stats["thought"] += thought_sum
            text_no_thought = _THOUGHT_RE.sub("", content).strip()
            tool_sum = sum(count_str(m.group(1)) for m in _TOOL_CALL_RE.finditer(text_no_thought))
            stats["tool"] += tool_sum
            text_response = _TOOL_CALL_RE.sub("", text_no_thought).strip()
            stats["response"] += count_str(text_response)
            counted = True

    if not counted and role == "assistant" and isinstance(content, dict):
        text = content.get("text", "") or content.get("content", "")
        if text and isinstance(text, str):
            stats["response"] += count_str(text)
    return stats


# ── Per-message stats LRU ───────────────────────────────────────────────────
# A long session re-sends the same 100-200 message dicts on every tool round;
# only the last 1-2 change. Re-encoding all of them cost 164-205 ms per round
# (measured, 181 messages / ~600 KB). Keying on the same identity+shape tuple
# the provider's message LRU already uses makes a hit mean "same object, same
# shape", so a stable history costs one dict lookup per message.
_MSG_STATS_CACHE: OrderedDict[Any, dict[str, int]] = OrderedDict()
_MSG_STATS_CACHE_MAX = 2048
_MSG_STATS_CACHE_LOCK = threading.Lock()


def _message_stats_cached(
    message: dict,
    count_str: Callable[[str | None], int],
    encoding: Any | None,
) -> dict[str, int]:
    """Memoized :func:`_message_stats`. Returned dict is read-only to callers."""
    try:
        key = (encoding, message_token_cache_key(message))
        hash(key)
    except TypeError:
        # Unhashable payload shape — fall back to counting without caching.
        return _message_stats(message, count_str)

    with _MSG_STATS_CACHE_LOCK:
        hit = _MSG_STATS_CACHE.get(key)
        if hit is not None:
            _MSG_STATS_CACHE.move_to_end(key)
            return hit

    part = _message_stats(message, count_str)

    with _MSG_STATS_CACHE_LOCK:
        _MSG_STATS_CACHE[key] = part
        while len(_MSG_STATS_CACHE) > _MSG_STATS_CACHE_MAX:
            _MSG_STATS_CACHE.popitem(last=False)
    return part


def _walk_tool_defs(tools: list, count_str: Callable[[str | None], int]) -> int:
    """Sum the token cost of raw tool definitions (uncached walk)."""
    total = 0
    for tool in tools:
        tool_d = _as_dict(tool)
        fn = _as_dict(tool_d.get("function") or tool)
        total += 6
        if fn.get("name"):
            total += count_str(str(fn["name"]))
        if fn.get("description"):
            total += count_str(str(fn["description"]))
        if fn.get("parameters") is not None:
            try:
                total += count_str(json.dumps(fn["parameters"], ensure_ascii=False))
            except Exception:
                total += count_str(str(fn["parameters"]))
    return total


# Tool schemas are a session constant (`_last_tools` is the same list object),
# yet they were re-serialized and re-encoded on every breakdown — ~16 ms for 77
# tools, which is what remained after the per-message cache landed.
_TOOL_DEFS_CACHE: OrderedDict[Any, int] = OrderedDict()
_TOOL_DEFS_CACHE_MAX = 32


def _tool_defs_tokens(
    tools: list | None,
    encoding: Any | None,
    count_str: Callable[[str | None], int],
) -> int:
    """Memoized :func:`_walk_tool_defs`, keyed on schema-list identity."""
    if not tools:
        return 0
    try:
        key = (
            encoding,
            id(tools),
            len(tools),
            id(tools[0]),
            id(tools[len(tools) // 2]),
            id(tools[-1]),
        )
        hash(key)
    except (TypeError, IndexError):
        return _walk_tool_defs(tools, count_str)

    with _MSG_STATS_CACHE_LOCK:
        hit = _TOOL_DEFS_CACHE.get(key)
        if hit is not None:
            _TOOL_DEFS_CACHE.move_to_end(key)
            return hit

    total = _walk_tool_defs(tools, count_str)

    with _MSG_STATS_CACHE_LOCK:
        _TOOL_DEFS_CACHE[key] = total
        while len(_TOOL_DEFS_CACHE) > _TOOL_DEFS_CACHE_MAX:
            _TOOL_DEFS_CACHE.popitem(last=False)
    return total


def compute_token_breakdown(
    messages: list[dict],
    tools: list[dict] | None = None,
    *,
    encoding: Any | None = None,
    total: int | None = None,
) -> dict[str, int]:
    """Return breakdown dict: system/user/thought/tool/tool_defs/response[/overhead]."""
    stats: dict[str, int] = {
        "system": 0,
        "user": 0,
        "thought": 0,
        "tool": 0,
        "tool_defs": 0,
        "response": 0,
    }
    _count_str = make_count_str(encoding)

    if tools:
        stats["tool_defs"] = _tool_defs_tokens(tools, encoding, _count_str)

    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        part = _message_stats_cached(msg, _count_str, encoding)
        for bucket, value in part.items():
            stats[bucket] += value

    if total is not None:
        breakdown_total = sum(stats.values())
        stats["overhead"] = max(0, int(total) - breakdown_total)
    return stats


def count_multimodal_content_tokens(content: list, encoding: Any | None = None) -> int:
    """Extra tokens inside content lists that plain text/image loops miss."""
    _count_str = make_count_str(encoding)
    n = 0
    for item in content or []:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t in ("tool_result", "functionResponse"):
            n += _count_str(extract_tool_result_text(item))
        elif t == "tool_use":
            n += _count_str(item.get("name", "") + str(item.get("input", {})))
    return n
