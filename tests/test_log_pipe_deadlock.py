"""Guards for the pipe-write deadlock reported on 2026-09-28.

Chain: a child's stderr pipe had no reader → the child blocked inside a
``logging`` call → the handler lock froze every logging thread → the launcher's
log-forwarding thread for the agent was never started → the agent's stdout pipe
was never read → the agent's event loop froze inside its own log write. These
tests cover the three fixes: the console handler no longer blocks the caller,
the drain thread starts before anything logs, and ``opensquad start`` drains the
children it spawns (tests/test_child_output.py).
"""

from __future__ import annotations

import ast
import io
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from opensquad.log_setup import _console_writer, _DroppingQueueHandler, nonblocking_console_handler

ROOT = Path(__file__).resolve().parents[1]
PROCESS_MANAGER = ROOT / "src" / "opensquad" / "launcher" / "process_manager.py"


class _StalledStream:
    """Stands in for a pipe nobody reads: writes block until released."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.writes = 0
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        # Bounded, so a regression fails the assertion instead of hanging CI.
        self.release.wait(timeout=30)
        with self._lock:
            self.writes += 1
        return len(text)

    def flush(self) -> None:
        pass


def _logger_with(handler: logging.Handler) -> logging.Logger:
    logger = logging.getLogger(f"pipe-deadlock-{id(handler)}")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    return logger


def test_console_handler_does_not_block_the_thread_that_logs():
    stream = _StalledStream()
    logger = _logger_with(nonblocking_console_handler(stream, max_queue=10))

    started = time.perf_counter()
    for i in range(500):
        logger.info("record %d", i)
    elapsed = time.perf_counter() - started

    # A plain StreamHandler would sit in the first write for the whole timeout.
    assert elapsed < 2.0, f"logging blocked the caller for {elapsed:.1f}s"
    assert stream.writes == 0  # the pump is stuck on the very first record

    stream.release.set()
    deadline = time.time() + 5
    while stream.writes < 1 and time.time() < deadline:
        time.sleep(0.01)
    assert stream.writes >= 1, "the pump never wrote anything once unblocked"

    # Queue capacity bounds the loss: the stalled record plus what the queue held.
    time.sleep(0.2)
    assert stream.writes <= 20, f"wrote {stream.writes} of 500 records — queue did not drop"


def test_a_full_queue_drops_the_console_copy_instead_of_raising():
    handler = _DroppingQueueHandler(queue.Queue(maxsize=1))
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)
    handler.enqueue(record)
    handler.enqueue(record)  # queue is full: must be swallowed, not propagated


def test_console_writer_uses_a_private_handle_for_real_streams(tmp_path):
    with (tmp_path / "console.log").open("w", encoding="utf-8") as stream:
        assert _console_writer(stream) is not stream


def test_console_writer_falls_back_for_non_files():
    stream = io.StringIO()
    assert _console_writer(stream) is stream


def test_setup_logging_puts_the_file_handler_before_the_console_copy(tmp_path):
    """The durable record must not sit behind a console that can stall."""
    from opensquad.log_setup import setup_logging

    logger = logging.getLogger("setup-logging-order")
    try:
        setup_logging(logger, "order.log", log_dir=str(tmp_path), force=True)
        kinds = [type(handler).__name__ for handler in logger.handlers]
        assert kinds[0] in {"SafeRotatingFileHandler", "RotatingFileHandler"}, kinds
        assert kinds[-1] == "_DroppingQueueHandler", kinds
    finally:
        logger.handlers.clear()


# ── End to end: a child that logs into a pipe nobody reads ──

_CHILD = """
import logging
import sys

from opensquad.log_setup import nonblocking_console_handler

logger = logging.getLogger("flood")
logger.handlers.clear()
logger.propagate = False
logger.setLevel(logging.INFO)
logger.addHandler(nonblocking_console_handler())
for i in range(20000):
    logger.info("log line %d %s", i, "x" * 60)
"""


def test_a_child_logging_into_an_unread_pipe_still_exits():
    """The 2026-09-28 shape: nobody drains the pipe.

    A plain StreamHandler wedges the child partway through the loop — that is
    the reported freeze. The console copy must instead absorb the full pipe on
    its own thread, let the child finish, and let it exit.
    """
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    p = subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    try:
        assert p.wait(timeout=30) == 0
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
    # Only the pipe's worth of records got through; the rest were dropped
    # rather than allowed to block anyone.
    assert len(p.stderr.read()) < 65536


# ── Launcher ordering: the drain thread must exist before anything logs ──


def _method_node(class_name: str, method_name: str) -> ast.FunctionDef:
    tree = ast.parse(PROCESS_MANAGER.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    raise AssertionError(f"{class_name}.{method_name} not found in {PROCESS_MANAGER.name}")


def _call_sites(func: ast.FunctionDef) -> list[tuple[int, str]]:
    sites: list[tuple[int, str]] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "start" and isinstance(f.value, ast.Attribute):
            if f.value.attr == "_log_thread":
                sites.append((node.lineno, "drain"))
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            if f.value.id == "_log":
                sites.append((node.lineno, "log"))
            elif f.attr == "Popen" and f.value.id == "subprocess":
                sites.append((node.lineno, "popen"))
        elif isinstance(f, ast.Name) and f.id == "_write_runtime_registry":
            sites.append((node.lineno, "registry"))
    return sorted(sites)


@pytest.mark.parametrize(
    ("class_name", "method_name"),
    [("AgentProcess", "start"), ("PluginServiceProcess", "_start_impl")],
)
def test_drain_thread_starts_before_the_first_log_call(class_name: str, method_name: str):
    """Logging before the drain thread starts can wedge the launcher itself.

    The child's 64 KiB pipe is unread until that thread runs, so a log call
    placed before it can block while holding the handler lock — and then the
    thread is never started at all.
    """
    sites = _call_sites(_method_node(class_name, method_name))
    popen = next(ln for ln, kind in sites if kind == "popen")
    drain = next(ln for ln, kind in sites if kind == "drain")
    late = [ln for ln, kind in sites if kind in ("log", "registry") and ln > popen]
    assert late, "expected at least one log/registry call after the spawn"
    assert drain < min(late), (
        f"{class_name}.{method_name}: the log-forwarding thread starts at line {drain}, "
        f"but something logs at line {min(late)} (after the spawn at {popen}) — the child's "
        "pipe is unread at that point"
    )
