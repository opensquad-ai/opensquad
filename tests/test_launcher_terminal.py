"""The terminal's launcher-side handlers — the layer the panel actually calls.

A terminal must not depend on the agent, so the shell lives in the launcher and the panel
talks to these handlers over HTTP (through the gateway). This exercises them end to end: open,
write a command, poll the output, stop the shell.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import opensquad.terminal_session as ts  # noqa: E402
from opensquad.launcher.management_api._filesystem import FilesystemMixin  # noqa: E402


class _Stub(FilesystemMixin):
    """The handlers, with the two things they borrow from the server stubbed."""

    def __init__(self, root: str):
        self._root = root
        self.sent: tuple[dict, int] | None = None

    def _agent_fs_root(self, name: str, root: str):  # noqa: ARG002 - the agent's workspace
        return self._root, None

    def _send_json(self, payload, status=200):  # noqa: ANN001
        self.sent = (payload, status)
        return payload


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch, tmp_path):
    """A real shell is started here: no terminal may outlive the test."""
    # The workspace gate is about the *agent's* shells; here the root is a temp dir.
    monkeypatch.setattr("opensquad.utils.path_utils.is_path_safe", lambda path: True)
    yield
    ts.close_all()


def test_open_write_read_close_through_the_handlers(tmp_path):
    stub = _Stub(str(tmp_path))

    opened = stub._handle_terminal_open("agent305", {"terminal_id": "term1"})
    assert opened["ok"] is True, opened
    assert opened["terminal_id"] == "term1"
    assert opened["agent"] == "agent305"
    assert stub.sent[1] == 200

    assert stub._handle_terminal_write("agent305", {"terminal_id": "term1", "text": "echo launcher-terminal\n"})["ok"]

    seen = ""
    deadline = time.time() + 10
    while time.time() < deadline:
        res = stub._handle_terminal_read("agent305", {"terminal_id": "term1", "since": 0})
        seen = res["chunk"]
        if "launcher-terminal" in seen:
            break
        time.sleep(0.05)
    assert "launcher-terminal" in seen, f"no output came back: {seen!r}"

    # …and the offset the caller already saw is not repeated
    offset = res["offset"]
    assert stub._handle_terminal_read("agent305", {"terminal_id": "term1", "since": offset})["chunk"] == ""

    assert stub._handle_terminal_close("agent305", {"terminal_id": "term1"})["ok"] is True
    late = stub._handle_terminal_read("agent305", {"terminal_id": "term1", "since": 0})
    assert late["ok"] is False  # the shell is gone, and the panel is told so


def test_interrupt_and_unknown_ids_are_reported(tmp_path):
    stub = _Stub(str(tmp_path))
    stub._handle_terminal_open("agent305", {"terminal_id": "term2"})

    assert stub._handle_terminal_interrupt("agent305", {"terminal_id": "term2"})["ok"] is True
    gone = stub._handle_terminal_write("agent305", {"terminal_id": "nope", "text": "x\n"})
    assert gone["ok"] is False and stub.sent[1] == 400


def test_a_start_failure_is_a_400_not_a_crash(tmp_path, monkeypatch):
    stub = _Stub(str(tmp_path))

    def _boom(**kwargs):
        return {"ok": False, "error": "no shell here"}

    monkeypatch.setattr(ts, "open_terminal", _boom)

    assert stub._handle_terminal_open("agent305", {"terminal_id": "term3"})["ok"] is False
    assert stub.sent[1] == 400


def test_the_gateway_proxies_every_terminal_op():
    """Wiring lock: the panel's five calls have to reach the launcher through the gateway."""
    admin = (_SRC / "opensquad" / "gateway" / "backend" / "app" / "ai_web" / "routes" / "_admin.py").read_text(
        encoding="utf-8"
    )
    base = (_SRC / "opensquad" / "launcher" / "management_api" / "_base.py").read_text(encoding="utf-8")

    for op in ("open", "write", "interrupt", "close", "read"):
        assert f'"/admin/agents/{{name}}/terminal/{op}"' in admin, op
        assert f"/terminal/{op}" in base, op
