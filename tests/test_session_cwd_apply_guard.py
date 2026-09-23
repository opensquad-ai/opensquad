"""Shell teardown must be scoped to the session it targets.

Three independent defects are locked here:

1. ``InputHub._check_session_cwd`` compared the raw path from the signal file
   against ``AgentContext.session_cwd`` — which ``filesystem.set_session_cwd``
   stores as ``normcase(abspath(p))``. The two never compare equal on Windows
   (drive-letter case), so the "already applied" guard never fired and every
   turn re-ran ``set_session_cwd``. Each run closed every live shell session
   (``opensquad.tools.system._SESSIONS``), which is what killed commands that
   were still executing mid-turn.
2. ``set_session_cwd`` closed *every* shell and then blanket-cleared
   ``_SESSIONS``, and its "Cleared N" log read ``len(_SESSIONS)`` after the
   clear, so it always printed 0. It now recycles only the shells the cwd
   change actually invalidates.
3. A session-scoped ``abort_all_tool_processes`` filtered Jobs but kept every
   shell, so a hung synchronous ``run_session_job`` (no job_id to match on)
   could never be unblocked; ``runner._withdraw_turn`` passed no sid at all,
   so withdrawing one pane froze the others.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

import opensquad._context as _context
import opensquad.input_hub as input_hub
import opensquad.tools.filesystem as filesystem
import opensquad.tools.system as system_mod
from opensquad.utils.session_cwd import session_cwd_path


class _FakeCtx:
    def __init__(self, session_cwd: str = ""):
        self.session_cwd = session_cwd


class _FakeSession:
    def __init__(self, working_directory: str = "", ui_sid: str = ""):
        self.working_directory = working_directory
        self.ui_sid = ui_sid
        self.closed = False

    def close(self):
        self.closed = True


def _write_signal(agent_dir: Path, raw_path: str) -> None:
    """Write the signal file with an UN-normalised path (bypasses the writer)."""
    cwd_file = Path(session_cwd_path(str(agent_dir)))
    cwd_file.write_text(json.dumps({"version": 1, "path": raw_path, "ts": 1.0}), encoding="utf-8")


@pytest.fixture()
def hub(tmp_path: Path, monkeypatch):
    agent_dir = tmp_path / "agents" / "coder"
    agent_dir.mkdir(parents=True)
    h = input_hub.InputHub()
    h.set_agent_context(str(agent_dir))
    return h, agent_dir


def test_non_normalised_signal_path_is_not_reapplied(hub, monkeypatch):
    """Raw `sub/.` must match the stored normcased form → no second apply."""
    h, agent_dir = hub
    target = agent_dir / "project"
    target.mkdir()

    # Same directory, written the way the old guard failed to recognise:
    # a redundant `/.` segment survives in the raw string but not in abspath().
    _write_signal(agent_dir, os.path.join(str(target), "."))

    applied: list[str] = []
    monkeypatch.setattr(filesystem, "set_session_cwd", lambda p: applied.append(p) or {"status": "success"})
    monkeypatch.setattr(_context, "get_current_context", lambda: _FakeCtx(str(target)))

    h._check_session_cwd()

    assert applied == [], "cwd was re-applied although it was already active"


def test_genuinely_new_path_is_still_applied(hub, monkeypatch):
    """The guard must not over-correct: a different directory is applied."""
    h, agent_dir = hub
    old = agent_dir / "old"
    new = agent_dir / "new"
    old.mkdir()
    new.mkdir()
    _write_signal(agent_dir, str(new))

    applied: list[str] = []
    monkeypatch.setattr(filesystem, "set_session_cwd", lambda p: applied.append(p) or {"status": "success"})
    monkeypatch.setattr(_context, "get_current_context", lambda: _FakeCtx(str(old)))

    h._check_session_cwd()

    assert applied == [str(new)]


def test_applying_the_same_cwd_kills_nothing(tmp_path: Path, monkeypatch, caplog):
    """Re-applying an unchanged cwd must not touch a shell (nor its command).

    This is the zh08 shape: the signal file held a path that compared unequal to
    ``ctx.session_cwd``, so the cwd was applied on every turn and each apply
    closed every shell — in-flight commands came back ``reason=session_stopped``.
    A shell already sitting in the target directory is unaffected by the change.
    """
    monkeypatch.setattr(filesystem, "_CONFIG_PATH", None)
    monkeypatch.setattr(filesystem, "_EXTRA_ALLOWED_DIRS", [], raising=False)
    target = tmp_path / "project"
    target.mkdir()
    # Same directory, different spelling: abspath/normcase must collapse them.
    running = _FakeSession(working_directory=os.path.join(str(target), "."))
    monkeypatch.setattr(system_mod, "_SESSIONS", {"default": running})

    with caplog.at_level(logging.INFO, logger="opensquad.tools.filesystem"):
        result = filesystem.set_session_cwd(str(target))

    assert result["status"] == "success"
    assert running.closed is False, "a shell already in the target dir was closed"
    assert {"default": running} == system_mod._SESSIONS
    assert "Recycled 0 shell session(s) for new cwd (kept 1 already in target)" in caplog.text


def test_only_shells_left_in_the_old_dir_are_recycled(tmp_path: Path, monkeypatch, caplog):
    """A cwd change recycles stale shells and keeps the ones already correct."""
    monkeypatch.setattr(filesystem, "_CONFIG_PATH", None)
    monkeypatch.setattr(filesystem, "_EXTRA_ALLOWED_DIRS", [], raising=False)
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    stale = _FakeSession(working_directory=str(old), ui_sid="pane-a")
    fresh = _FakeSession(working_directory=str(new), ui_sid="pane-b")
    monkeypatch.setattr(system_mod, "_SESSIONS", {"stale": stale, "fresh": fresh})

    with caplog.at_level(logging.INFO, logger="opensquad.tools.filesystem"):
        result = filesystem.set_session_cwd(str(new))

    assert result["status"] == "success"
    assert stale.closed is True
    assert fresh.closed is False
    assert list(system_mod._SESSIONS) == ["fresh"]
    assert "Recycled 1 shell session(s) for new cwd (kept 1 already in target)" in caplog.text


class _NoChildren:
    def children(self, recursive: bool = False):
        return []


def test_scoped_abort_closes_only_the_stopped_sessions_shells(monkeypatch):
    """A session-scoped abort must free *its* shell and leave the sibling's."""
    mine = _FakeSession(working_directory="C:/w", ui_sid="pane-a")
    theirs = _FakeSession(working_directory="C:/w", ui_sid="pane-b")
    monkeypatch.setattr(system_mod, "_SESSIONS", {"a": mine, "b": theirs})
    monkeypatch.setattr(system_mod, "_JOBS", {})
    monkeypatch.setattr(system_mod, "_JOB_UI_META", {})

    out = system_mod.abort_all_tool_processes("stop_session", session_id="pane-a")

    assert out["sessions"] == 1
    assert mine.closed is True
    assert theirs.closed is False
    assert list(system_mod._SESSIONS) == ["b"]


def test_global_abort_still_closes_every_shell(monkeypatch):
    """No sid = explicit stop-all: nothing may be skipped."""
    a = _FakeSession(ui_sid="pane-a")
    b = _FakeSession(ui_sid="pane-b")
    monkeypatch.setattr(system_mod, "_SESSIONS", {"a": a, "b": b})
    monkeypatch.setattr(system_mod, "_JOBS", {})
    monkeypatch.setattr(system_mod, "_JOB_UI_META", {})
    # The global branch sweeps the agent's child processes — keep that off the
    # pytest process itself.
    monkeypatch.setattr(system_mod.psutil, "Process", lambda *a, **k: _NoChildren())
    monkeypatch.setattr(system_mod.psutil, "wait_procs", lambda *a, **k: ([], []))

    system_mod.abort_all_tool_processes("stop_task")

    assert a.closed is True and b.closed is True
    assert system_mod._SESSIONS == {}


def test_shell_session_records_owning_chat_sid(monkeypatch):
    """`ShellSession.ui_sid` mirrors `Job.ui_sid`: it comes from the tool call."""
    monkeypatch.setattr(system_mod.ShellSession, "_start_process", lambda self: None)
    monkeypatch.setattr(system_mod, "_resolve_working_directory", lambda wd: "C:/w")

    token = system_mod.set_tool_call_context(sid="pane-a", call_id="c1")
    try:
        assert system_mod.ShellSession(session_id="default").ui_sid == "pane-a"
    finally:
        system_mod.reset_tool_call_context(token)

    assert system_mod.ShellSession(session_id="default").ui_sid == ""


@pytest.fixture()
def withdraw_runner(application_context):
    from opensquad.runner import AgentRunner

    return AgentRunner(
        chat_api=application_context.chat_api,
        tool_registry=application_context.tool_registry,
        agent_context=application_context,
    )


async def _noop(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_withdraw_turn_aborts_only_its_own_session(withdraw_runner, monkeypatch):
    """Withdrawing one pane's turn must not touch the sibling panes."""
    seen: list[tuple[str, str | None]] = []

    def _fake_abort(reason: str, session_id: str | None = None):
        seen.append((reason, session_id))
        return {}

    class _SM:
        def truncate_from_timestamp(self, *a, **k):
            return {"ok": True, "messages": 0, "events": 0, "cut_index": 0}

        def get_messages(self):
            return []

        def get_events(self):
            return []

        def get_current_session_id(self):
            return "pane-a"

    withdraw_runner._turn_sid = "pane-a"
    monkeypatch.setattr(system_mod, "abort_all_tool_processes", _fake_abort)
    monkeypatch.setattr("opensquad.runner._get_session_manager", lambda: _SM())
    monkeypatch.setattr("opensquad.runner.bus.emit_async", _noop)
    monkeypatch.setattr(withdraw_runner, "_load_history", lambda *a, **k: None)
    monkeypatch.setattr(withdraw_runner, "_broadcast_token_stats", _noop)
    monkeypatch.setattr(withdraw_runner, "_emit", _noop)
    monkeypatch.setattr(withdraw_runner, "_finalize_user_stop", _noop)
    monkeypatch.setattr(withdraw_runner, "_maybe_emit_idle", _noop)

    await withdraw_runner._withdraw_turn("2026-09-22T00:00:00Z")

    assert seen == [("withdraw_turn", "pane-a")]


@pytest.mark.asyncio
async def test_withdraw_turn_falls_back_to_focused_then_global(withdraw_runner, monkeypatch):
    """No bound turn sid → focused/current pane; nothing resolvable → global."""
    seen: list[str | None] = []

    def _fake_abort(reason: str, session_id: str | None = None):
        seen.append(session_id)
        return {}

    class _SM:
        focused = "pane-b"

        def truncate_from_timestamp(self, *a, **k):
            return {"ok": True, "messages": 0, "events": 0, "cut_index": 0}

        def get_messages(self):
            return []

        def get_events(self):
            return []

        def get_focused_session_id(self):
            return self.focused

        def get_current_session_id(self):
            return "pane-current"

    sm = _SM()
    withdraw_runner._turn_sid = ""
    monkeypatch.setattr(system_mod, "abort_all_tool_processes", _fake_abort)
    monkeypatch.setattr("opensquad.runner._get_session_manager", lambda: sm)
    monkeypatch.setattr("opensquad.runner.bus.emit_async", _noop)
    monkeypatch.setattr(withdraw_runner, "_load_history", lambda *a, **k: None)
    monkeypatch.setattr(withdraw_runner, "_broadcast_token_stats", _noop)
    monkeypatch.setattr(withdraw_runner, "_emit", _noop)
    monkeypatch.setattr(withdraw_runner, "_finalize_user_stop", _noop)
    monkeypatch.setattr(withdraw_runner, "_maybe_emit_idle", _noop)

    await withdraw_runner._withdraw_turn("2026-09-22T00:00:00Z")
    sm.focused = ""
    await withdraw_runner._withdraw_turn("2026-09-22T00:01:00Z")

    # Focused pane wins; with no focus the current session is used rather than
    # reverting to an agent-wide kill.
    assert seen == ["pane-b", "pane-current"]
