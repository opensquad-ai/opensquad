"""A piped child stream must be drained while the child runs, not after it exits.

Reading a pipe only once the child is gone deadlocks: the OS pipe buffer (64 KiB
on Windows) fills up and the child's next write blocks forever. On 2026-09-28
that write was a ``logging`` call made while holding the handler lock, which
froze every thread that logs — the launcher, then the agent it had just started.
"""

from __future__ import annotations

import subprocess
import sys

from opensquad.child_output import StreamTail

# os.write bypasses Python's buffering, so the pipe fills after exactly the
# number of bytes asked for; 64 chunks of 4096 B is 256 KiB, 4x the buffer.
_FLOOD = "import os\nfor _ in range(64):\n    os.write(2, b'x' * 4095 + b'\\n')\n"

_LINES = "import sys\nfor i in range(3000):\n    sys.stderr.write('line-%d\\n' % i)\n"


def _spawn(code: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def test_child_writing_past_the_pipe_buffer_still_exits():
    """The regression: with nobody reading, this child blocks forever."""
    p = _spawn(_FLOOD)
    tail = StreamTail(p.stderr)
    try:
        assert p.wait(timeout=30) == 0
    finally:
        if p.poll() is None:
            p.kill()
    tail.join(timeout=5)
    assert tail.tail(1) == ["x" * 4095]


def test_tail_keeps_the_last_lines_in_order():
    p = _spawn(_LINES)
    tail = StreamTail(p.stderr, max_lines=10)
    assert p.wait(timeout=30) == 0
    # join() is what makes the buffer trustworthy: without it the reader may
    # still be mid-read when the caller asks for the crash output.
    tail.join(timeout=5)
    assert tail.tail(0) == [f"line-{i}" for i in range(2990, 3000)]
    assert tail.tail(2) == ["line-2998", "line-2999"]
