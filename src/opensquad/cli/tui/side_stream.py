"""Ring buffers for TUI side-stream views (sub-agent / shell job stdout)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SideStream:
    """One live side channel (sub-agent or shell job)."""

    key: str
    kind: str  # "sub" | "shell"
    title: str
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=2000))
    active: bool = True
    # True when the last line is an incomplete stream (more tokens may follow)
    _tail_open: bool = False

    def append(self, text: str, *, fresh: bool = False) -> None:
        """Append streaming text.

        Token/chunk streams coalesce onto the same line until a newline arrives
        (bash/cat style). Pass ``fresh=True`` to start a new block (tool call,
        status line, section header) without sticking to the previous token line.
        """
        if text is None:
            return
        s = str(text)
        if s == "":
            return

        if fresh:
            self._tail_open = False

        # Resume an incomplete line so "你"+"好" → "你好" instead of two rows
        if self._tail_open and self.lines:
            s = self.lines.pop() + s
            self._tail_open = False

        parts = s.split("\n")
        for part in parts[:-1]:
            self.lines.append(part)

        if s.endswith("\n"):
            # Trailing newline closed the line; empty last part is not kept open
            self._tail_open = False
        else:
            self.lines.append(parts[-1])
            self._tail_open = True

    def dump(self) -> str:
        return "\n".join(self.lines)


class SideStreamHub:
    """Manage multiple side streams; track the most recently active key."""

    def __init__(self) -> None:
        self.streams: dict[str, SideStream] = {}
        self.active_key: str | None = None

    def ensure(self, key: str, *, kind: str, title: str) -> SideStream:
        s = self.streams.get(key)
        if s is None:
            s = SideStream(key=key, kind=kind, title=title)
            self.streams[key] = s
        else:
            s.title = title or s.title
            s.kind = kind or s.kind
            s.active = True
        self.active_key = key
        return s

    def append(
        self,
        key: str,
        text: str,
        *,
        kind: str = "sub",
        title: str = "",
        fresh: bool = False,
    ) -> None:
        s = self.ensure(key, kind=kind, title=title or key)
        s.append(text, fresh=fresh)

    def mark_done(self, key: str) -> None:
        s = self.streams.get(key)
        if s:
            s.active = False
            s._tail_open = False

    def list_keys(self) -> list[str]:
        # active first, then by key
        keys = list(self.streams.keys())
        keys.sort(key=lambda k: (0 if self.streams[k].active else 1, k))
        return keys

    def get(self, key: str | None = None) -> SideStream | None:
        k = key or self.active_key
        if not k:
            return None
        return self.streams.get(k)

    def clear_inactive(self) -> None:
        dead = [k for k, s in self.streams.items() if not s.active and len(s.lines) == 0]
        for k in dead:
            self.streams.pop(k, None)


def payload_is_sub_agent(payload: Any) -> bool:
    if isinstance(payload, dict):
        if payload.get("sub_agent"):
            return True
        # nested content
        inner = payload.get("data") or payload.get("content")
        if isinstance(inner, dict) and inner.get("sub_agent"):
            return True
    return False


def side_key_from_payload(payload: Any, default: str = "sub") -> tuple[str, str]:
    """Return (key, title) for buffering."""
    if not isinstance(payload, dict):
        return default, default
    job = str(payload.get("job_id") or "").strip()
    label = str(payload.get("sub_task_label") or payload.get("label") or "").strip()
    if job:
        return f"job:{job}", label or f"job {job[:12]}"
    if label:
        return f"sub:{label[:48]}", label[:60]
    return default, "sub-agent"


def is_delegate_tool(name: str) -> bool:
    n = (name or "").lower()
    return "delegate_task" in n and "result" not in n and "list" not in n


def is_shell_tool(name: str) -> bool:
    n = (name or "").lower()
    needles = (
        "run_session_job",
        "create_shell",
        "start_job",
        "system__run",
        "powershell",
        "bash",
        "cmd.exe",
        "shell_session",
    )
    return any(x in n for x in needles)
