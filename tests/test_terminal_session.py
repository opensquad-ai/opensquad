"""The right panel's terminal: a real shell the user drives, streamed over the events the
app already uses for job output.

Two things are being pinned here. First, that an interactive shell actually works: a command
is written to its stdin and its output comes back as `job_stdout` keyed to *that* terminal —
the whole reason the panel could be built without a new websocket event type or a gateway
route. Second, that it cannot be pointed outside the workspace, and that a stale terminal id
is answered rather than ignored.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import opensquad.terminal_session as ts  # noqa: E402


@pytest.fixture(autouse=True)
def clean_registry():
    """No shell is left running by a test (each one starts a real process)."""
    yield
    ts.close_all()


@pytest.fixture()
def events(monkeypatch):
    """Capture the EventBus frames the terminal emits, exactly as the gateway sees them.

    Patched on the real module (`opensquad.bus` is already imported by the time a terminal
    runs, so replacing `sys.modules['opensquad.bus']` would be bypassed).
    """
    captured: list[tuple[str, dict]] = []

    from opensquad.events import bus as real_bus

    monkeypatch.setattr(real_bus, "emit", lambda etype, payload: captured.append((etype, payload)))
    return captured


def _wait_for(events, needle: str, timeout: float = 10.0) -> str:
    """Poll the captured stream for `needle`; returns all stdout text seen."""
    deadline = time.time() + timeout
    seen = ""
    while time.time() < deadline:
        seen = "".join(
            str((payload.get("data") or {}).get("chunk") or "") for etype, payload in events if etype == "job_stdout"
        )
        if needle in seen:
            return seen
        time.sleep(0.05)
    return seen


def test_a_terminal_runs_a_command_and_streams_its_output(events):
    opened = ts.open_terminal(terminal_id="t1")

    assert opened["ok"] is True, opened
    assert opened["terminal_id"] == "t1"
    assert opened["shell"] in ("cmd", "bash")

    assert ts.write_terminal("t1", "echo opensquad-terminal-ok\n")["ok"] is True

    seen = _wait_for(events, "opensquad-terminal-ok")
    assert "opensquad-terminal-ok" in seen, f"no output came back. saw: {seen!r}"

    # …and it arrived as job output keyed to this terminal, which is what the panel filters
    # on: that is also why no new websocket event type was needed.
    stdout = [p for e, p in events if e == "job_stdout"]
    assert stdout, "nothing was emitted"
    first = stdout[0]["data"]
    assert first["job_id"] == "t1"
    assert first["call_id"] == "terminal:t1"
    assert first["sid"] == ""


def test_the_terminal_reports_its_own_state(events):
    ts.open_terminal(terminal_id="t2")

    states = [p["data"].get("state") for e, p in events if e == "job_status"]

    assert "running" in states
    assert ts.list_terminals()[0]["running"] is True


def test_a_terminal_outside_the_workspace_is_refused(events):
    outside = "C:\\Windows" if sys.platform == "win32" else "/etc"

    result = ts.open_terminal(terminal_id="t3", cwd=outside)

    assert result["ok"] is False
    assert "workspace" in result["error"].lower()
    assert ts.list_terminals() == []  # nothing was registered
    assert [e for e, _ in events if e == "job_stdout"] == []


def test_a_trusted_terminal_may_start_outside_it(events):
    """The user's own terminal is not an agent action. `is_path_safe` confines an *agent's*
    shells to its workspace — but an agent's workspace can legitimately live outside
    get_workspace_root() (the reported case: on the Desktop), and the launcher has already
    vetted that directory, so the fence must not refuse the folder the panel is showing."""
    outside = tempfile.mkdtemp(prefix="opensquad-terminal-")

    result = ts.open_terminal(terminal_id="t3b", cwd=outside, trusted=True)

    assert result["ok"] is True, result
    assert result["cwd"].lower().rstrip("\\/") == outside.lower().rstrip("\\/")


def test_opening_the_same_id_twice_reuses_the_live_shell(events):
    first = ts.open_terminal(terminal_id="t4")
    second = ts.open_terminal(terminal_id="t4")

    assert first["ok"] is True and second["ok"] is True
    assert second.get("reused") is True
    assert len(ts.list_terminals()) == 1


def test_writing_to_an_unknown_terminal_is_reported():
    assert ts.write_terminal("nope", "echo hi\n") == {"ok": False, "error": "unknown terminal: nope"}
    assert ts.interrupt_terminal("nope")["ok"] is False


def test_closing_stops_the_shell_and_forgets_it(events):
    ts.open_terminal(terminal_id="t5")

    closed = ts.close_terminal("t5")

    assert closed["ok"] is True
    assert ts.list_terminals() == []
    assert ts.write_terminal("t5", "echo hi\n")["ok"] is False
    # a second close is not an error (the panel may unmount twice)
    assert ts.close_terminal("t5")["ok"] is True


def test_an_emptied_workspace_directory_still_resolves(events):
    """No cwd given means the workspace root — the same default the agent's shells use."""
    result = ts.open_terminal(terminal_id="t6", cwd="")

    assert result["ok"] is True
    assert result["cwd"]
    assert Path(result["cwd"]).is_absolute()


def test_close_all_stops_every_terminal(events):
    ts.open_terminal(terminal_id="t7")
    ts.open_terminal(terminal_id="t8")

    assert ts.close_all() == 2
    assert ts.list_terminals() == []


def test_the_adapter_dispatches_the_terminal_commands():
    """Wiring lock: the panel's commands have to be handled by the agent, or the tab is
    inert. They ride the existing command channel, so this is the only place to check."""
    adapter = (_SRC / "opensquad" / "gateway_adapter.py").read_text(encoding="utf-8")

    for command in ("terminal_open", "terminal_write", "terminal_interrupt", "terminal_close"):
        assert f'"{command}"' in adapter, f"{command} is not dispatched"
    assert "terminal_session" in adapter
