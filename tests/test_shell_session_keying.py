"""A shell that isn't named belongs to the chat session that asked for it.

``system.run_session_job`` (and friends) used to fall back to the constant
``"default"`` shell whenever the model did not pass ``session_id``. Real history
shows 801 of those calls carry no label, so two parallel chat panes shared one
shell. ``ShellSession.execute`` does not serialise whole commands — it writes
``command + marker`` into one stdin and polls one output buffer — so the panes'
outputs and results could be attributed to the wrong pane, and a session-scoped
abort (which frees shells by ``ui_sid``) had no way to free the shell that was
actually hanging.

The key is now the calling chat sid. An explicit label still wins: real sessions
use labels like ``gitcheck`` / ``mc_build`` to keep several shells on purpose.
"""

from __future__ import annotations

import pytest

import opensquad.tools.system as system_mod


class _FakeSession:
    def __init__(self, working_directory: str = "C:/w", shell_type: str = "bash", ui_sid: str = ""):
        self.working_directory = working_directory
        self.shell_type = shell_type
        self.ui_sid = ui_sid
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture()
def shells(monkeypatch):
    """Isolate the module-global shell registry and never spawn a real shell."""
    monkeypatch.setattr(system_mod, "_SESSIONS", {})
    monkeypatch.setattr(system_mod, "_resolve_working_directory", lambda wd: "C:/w")
    monkeypatch.setattr(system_mod, "_is_path_safe", lambda p: True)
    monkeypatch.setattr(system_mod.ShellSession, "_start_process", lambda self: None)
    return system_mod._SESSIONS


class _As:
    """Bind a chat sid for the duration of the block, then restore."""

    def __init__(self, sid: str):
        self.sid = sid
        self.token = None

    def __enter__(self):
        self.token = system_mod.set_tool_call_context(sid=self.sid, call_id=f"call-{self.sid}")
        return self

    def __exit__(self, *exc):
        system_mod.reset_tool_call_context(self.token)
        return False


def test_each_chat_session_gets_its_own_shell(shells):
    with _As("pane-a"):
        a = system_mod._get_or_create_session()
    with _As("pane-b"):
        b = system_mod._get_or_create_session()

    assert a is not b, "two panes were handed the same shell"
    assert set(shells) == {"pane-a", "pane-b"}
    assert a.ui_sid == "pane-a" and b.ui_sid == "pane-b"


def test_the_same_chat_session_reuses_one_shell(shells):
    with _As("pane-a"):
        first = system_mod._get_or_create_session()
        second = system_mod._get_or_create_session()

    assert first is second
    assert set(shells) == {"pane-a"}


def test_explicit_shell_label_beats_the_chat_sid(shells):
    """`gitcheck` / `mc_build` labels must still name their own shell."""
    with _As("pane-a"):
        labelled = system_mod._get_or_create_session("gitcheck")
        default_for_pane = system_mod._get_or_create_session()

    assert labelled.session_id == "gitcheck"
    assert default_for_pane.session_id == "pane-a"
    assert set(shells) == {"gitcheck", "pane-a"}


def test_no_chat_context_still_lands_on_default(shells):
    """CLI and other sid-less callers keep the old shared-key behaviour."""
    with _As(""):
        sess = system_mod._get_or_create_session()

    assert sess.session_id == system_mod._DEFAULT_SESSION_ID
    assert set(shells) == {system_mod._DEFAULT_SESSION_ID}


def test_close_shell_session_targets_the_calling_pane(monkeypatch):
    mine = _FakeSession(ui_sid="pane-a")
    other = _FakeSession(ui_sid="pane-b")
    monkeypatch.setattr(system_mod, "_SESSIONS", {"pane-a": mine, "pane-b": other})

    with _As("pane-a"):
        out = system_mod.close_shell_session()

    assert out["status"] == "success"
    assert mine.closed is True
    assert other.closed is False, "closing pane A's shell closed pane B's too"


def test_restart_shell_session_targets_the_calling_pane(monkeypatch):
    mine = _FakeSession(working_directory="C:/mine", shell_type="cmd", ui_sid="pane-a")
    other = _FakeSession(working_directory="C:/theirs", shell_type="bash", ui_sid="pane-b")
    monkeypatch.setattr(system_mod, "_SESSIONS", {"pane-a": mine, "pane-b": other})
    monkeypatch.setattr(system_mod, "_is_path_safe", lambda p: True)
    monkeypatch.setattr(system_mod, "_resolve_working_directory", lambda wd: wd or "C:/w")
    monkeypatch.setattr(system_mod.ShellSession, "_start_process", lambda self: None)

    with _As("pane-a"):
        out = system_mod.restart_shell_session()

    assert out["status"] == "success"
    assert out["working_directory"] == "C:/mine" and out["shell_type"] == "cmd"
    assert mine.closed is True
    assert other.closed is False
    # Rebuilt under the pane's own key, preserving that shell's cwd/shell type.
    assert set(system_mod._SESSIONS) == {"pane-a", "pane-b"}


def test_run_session_job_executes_in_the_calling_chat_shell(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(system_mod, "_SESSIONS", {})
    monkeypatch.setattr(system_mod, "_resolve_working_directory", lambda wd: "C:/w")
    monkeypatch.setattr(system_mod, "_is_path_safe", lambda p: True)
    monkeypatch.setattr(system_mod.ShellSession, "_start_process", lambda self: None)
    monkeypatch.setattr(
        system_mod.ShellSession,
        "execute",
        lambda self, command, timeout=120.0: seen.append(self.session_id) or {"status": "success"},
    )
    monkeypatch.setattr(system_mod, "_shell_watch_begin", lambda: None)
    monkeypatch.setattr(system_mod, "_shell_watch_end", lambda root: None)

    with _As("pane-a"):
        system_mod.run_session_job("echo hi")
    with _As("pane-b"):
        system_mod.run_session_job("echo hi")

    assert seen == ["pane-a", "pane-b"]
