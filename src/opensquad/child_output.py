"""Drain a child process's piped output in the background.

Reading a piped stream only *after* the child exits is a deadlock waiting to
happen: the pipe buffer fills up (4 KiB on Windows, 64 KiB elsewhere) and every
later write from the child blocks forever. When that write is a log call — the
console handler writes to stderr — the block happens inside ``logging``, holding
the handler lock.

That is exactly how the whole OpenSquad process tree froze once (2026-09-28):
``opensquad start`` left the launcher's stderr pipe unread, the launcher blocked
in ``StreamHandler.emit`` on its "websearch: dependencies not available" line,
every launcher thread that wanted to log queued up behind the handler lock, so
the log-forwarding thread for the agent it had just spawned was never started,
so the agent's own stdout pipe was never read, so the agent's asyncio event loop
froze inside its own log write. Nothing in any log said "deadlock"; the agents
just never registered and the UI said "reconnecting" forever.

``StreamTail`` starts one daemon reader with the process and keeps the last N
lines, which is what callers should report when the child dies (a later
``stream.read()`` returns nothing — the reader consumed it).
"""

from __future__ import annotations

import collections
import threading
from typing import IO, Iterator


class StreamTail:
    """Continuously drain *stream*, remembering the last ``max_lines`` lines."""

    def __init__(
        self,
        stream: IO,
        *,
        max_lines: int = 200,
        name: str = "opensquad-output-drain",
    ) -> None:
        self._stream = stream
        self._lines: collections.deque[str] = collections.deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._pump, daemon=True, name=name)
        self._thread.start()

    def _pump(self) -> None:
        try:
            for line in self._iter_lines():
                with self._lock:
                    self._lines.append(line)
        except Exception:
            # Child closed the pipe, or the process tree is being torn down.
            pass

    def _iter_lines(self) -> Iterator[str]:
        for raw in self._stream:
            if isinstance(raw, bytes):
                yield raw.decode("utf-8", errors="replace").rstrip("\r\n")
            else:
                yield str(raw).rstrip("\r\n")

    def join(self, timeout: float | None = None) -> None:
        """Wait for the reader to hit EOF so the buffer holds everything.

        Call this after the child exits and *before* reading :meth:`tail`:
        the pipe closes on exit, so the reader finishes quickly — but it is
        still asynchronous, and reading too early loses the line that
        explains why the child died.
        """
        self._thread.join(timeout)

    def tail(self, n: int = 5) -> list[str]:
        """Return up to *n* most recent lines, oldest first (n<=0: all)."""
        with self._lock:
            lines = list(self._lines)
        return lines[-n:] if n > 0 else lines
