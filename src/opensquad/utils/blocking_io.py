"""Async wrappers for file IO, so the gateway event loop never does it inline.

The gateway is a single-event-loop FastAPI app. One synchronous
``open(...).write()`` of a user-supplied payload freezes **every** WebSocket
push and API request for the whole process — the same failure mode as the
``subprocess.run(ffmpeg, timeout=60)`` freeze that
``tests/test_asr_event_loop_not_blocked.py`` measures for real. A 50MB upload
write is a 50MB stall on the loop.

The synchronous implementations deliberately live at module level: that is the
shape ``tests/test_async_no_blocking_calls.py`` documents as correct (a plain
``def`` dispatched through ``asyncio.to_thread``), and it keeps every blocking
``open`` out of an ``async def`` body.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

__all__ = [
    "read_bytes",
    "read_bytes_sync",
    "read_json",
    "read_json_sync",
    "read_text",
    "read_text_sync",
    "write_bytes",
    "write_bytes_sync",
    "write_json",
    "write_json_sync",
    "write_text",
    "write_text_sync",
]


# ── synchronous primitives (safe only off the event loop) ──────────────


def write_bytes_sync(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


def write_text_sync(path: str, text: str, *, encoding: str = "utf-8") -> None:
    with open(path, "w", encoding=encoding) as f:
        f.write(text)


def read_bytes_sync(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def read_text_sync(path: str, *, encoding: str = "utf-8", errors: str = "replace") -> str:
    with open(path, encoding=encoding, errors=errors) as f:
        return f.read()


def write_json_sync(path: str, payload: Any, *, indent: int | None = 2, encoding: str = "utf-8") -> None:
    with open(path, "w", encoding=encoding) as f:
        json.dump(payload, f, ensure_ascii=False, indent=indent)


def read_json_sync(path: str, *, encoding: str = "utf-8") -> Any:
    with open(path, encoding=encoding) as f:
        return json.load(f)


# ── async wrappers (use these from async def bodies) ───────────────────


async def write_bytes(path: str, data: bytes) -> None:
    await asyncio.to_thread(write_bytes_sync, path, data)


async def write_text(path: str, text: str, *, encoding: str = "utf-8") -> None:
    await asyncio.to_thread(write_text_sync, path, text, encoding=encoding)


async def read_bytes(path: str) -> bytes:
    return await asyncio.to_thread(read_bytes_sync, path)


async def read_text(path: str, *, encoding: str = "utf-8", errors: str = "replace") -> str:
    return await asyncio.to_thread(read_text_sync, path, encoding=encoding, errors=errors)


async def write_json(path: str, payload: Any, *, indent: int | None = 2, encoding: str = "utf-8") -> None:
    await asyncio.to_thread(write_json_sync, path, payload, indent=indent, encoding=encoding)


async def read_json(path: str, *, encoding: str = "utf-8") -> Any:
    return await asyncio.to_thread(read_json_sync, path, encoding=encoding)
