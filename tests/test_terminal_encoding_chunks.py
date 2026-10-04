"""A character split across two reads must survive, and the same output must never be served twice.

Reported from the panel: the Windows banner came back as garbage, with the copyright line repeated
and a run of replacement marks. Two causes. The reader decoded each read on its own, so a cp936
character straddling the boundary became two replacement marks. And the panel's poll had no
single-flight guard, so two responses arriving out of order let the older offset win and the next
poll asked again for text already on screen.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import opensquad.terminal_session as ts  # noqa: E402


class _Stream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read(self, _size):
        return self._chunks.pop(0) if self._chunks else b""


class _Process:
    def __init__(self, chunks):
        self.stdout = _Stream(chunks)

    def wait(self, timeout=None):
        return 0


def _session(chunks, encoding: str = "cp936"):
    session = ts.TerminalSession.__new__(ts.TerminalSession)
    session.id = "t-chunks"
    session.encoding = encoding
    session.buffer = ""
    session.offset = 0
    session.exited = False
    session.return_code = None
    session.error = None
    session._lock = threading.Lock()
    session.process = _Process(chunks)
    session._emit = lambda *args, **kwargs: None
    return session


def test_a_character_split_across_two_reads_survives_intact():
    banner = "保留所有权。".encode("cp936")
    session = _session([b"(c) " + banner[:3], banner[3:]])

    session._pump()

    assert session.buffer == "(c) 保留所有权。"
    assert "\ufffd" not in session.buffer, "a split character must not become replacement marks"


def test_the_console_code_page_decodes_the_banner():
    line = "(c) Microsoft Corporation。保留所有权利。".encode("cp936")
    session = _session([line[:9], line[9:]])

    session._pump()

    assert "保留所有权利" in session.buffer
    assert session.buffer.count("保留所有权利") == 1


def test_output_already_shown_is_never_served_again():
    session = _session(["one".encode("cp936")])

    session._pump()

    assert session.read(since=session.offset)["chunk"] == ""
    assert session.read(since=0)["chunk"] == "one", "asking from the start replays once, on purpose"
    assert session.offset == len(session.buffer) == 3


def test_a_partial_utf8_sequence_is_held_for_the_next_read():
    text = "文件 ✅"
    data = text.encode("utf-8")
    session = _session([data[:2], data[2:]], encoding="utf-8")

    session._pump()

    assert session.buffer == text
    assert "\ufffd" not in session.buffer
