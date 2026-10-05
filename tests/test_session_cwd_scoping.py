"""A working directory belongs to the session that chose it, not to the whole agent.

Reported: asking an agent to check the current project in `1003/raven` inspected
`opensquad_deploy_test` instead. The agent had one shared `.session_cwd`, and with two workspaces open
at once each pane's folder choice rewrote it — the signal file was seen flipping between the two
within minutes, so a question asked in one workspace was answered against the other.

The session-scoped file already existed and was already written; what was missing was the read side.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad.utils.project_fs import resolve_agent_root  # noqa: E402
from opensquad.utils.session_cwd import read_session_cwd, session_cwd_path, write_session_cwd  # noqa: E402

INPUT_HUB = (_SRC / "opensquad" / "input_hub.py").read_text(encoding="utf-8")

RAVEN = "C:/ai_work/pro0/1003/raven"
REPO = "C:/ai_work/pro0/opensquad_deploy_test"


def _dirs(tmp_path) -> tuple[str, str, str]:
    agent_dir = tmp_path / "agents" / "agent305"
    raven = tmp_path / "1003" / "raven"
    repo = tmp_path / "opensquad_deploy_test"
    for path in (agent_dir, raven, repo):
        path.mkdir(parents=True, exist_ok=True)
    return str(agent_dir), str(raven), str(repo)


def test_a_session_keeps_its_own_directory_when_another_session_writes(tmp_path):
    agent_dir, raven, repo = _dirs(tmp_path)
    write_session_cwd(agent_dir, raven, "session-A")
    write_session_cwd(agent_dir, repo, "session-B")

    assert read_session_cwd(agent_dir, "session-A")["path"] == str(Path(raven).resolve())
    assert read_session_cwd(agent_dir, "session-B")["path"] == str(Path(repo).resolve())


def test_a_session_with_no_file_of_its_own_reports_nothing(tmp_path):
    """It must not borrow another pane's pick — the caller uses the session's own workspace then.

    This was the second half of the report: asking in the raven session answered raven, then asking
    in the deploy_test session also answered raven, because that session had no file and read the
    shared one.
    """
    agent_dir, raven, _repo = _dirs(tmp_path)
    write_session_cwd(agent_dir, raven)

    assert read_session_cwd(agent_dir, "session-never-seen") is None


def test_a_reader_with_no_session_id_still_sees_the_agent_level_file(tmp_path):
    """The serial path and the launcher's own read have no session, and are unchanged."""
    agent_dir, raven, _repo = _dirs(tmp_path)
    write_session_cwd(agent_dir, raven)

    assert read_session_cwd(agent_dir, "")["path"] == str(Path(raven).resolve())
    assert resolve_agent_root(agent_dir, "", "").endswith("raven")


def test_the_two_files_are_different_files(tmp_path):
    agent_dir, _raven, _repo = _dirs(tmp_path)
    write_session_cwd(agent_dir, RAVEN, "session-A")
    write_session_cwd(agent_dir, REPO)

    assert session_cwd_path(agent_dir, "session-A") != session_cwd_path(agent_dir)


def test_the_sandbox_root_follows_the_session_when_given_one(tmp_path):
    agent_dir, raven, repo = _dirs(tmp_path)
    write_session_cwd(agent_dir, raven, "session-A")
    write_session_cwd(agent_dir, repo, "session-B")

    assert resolve_agent_root(agent_dir, "", "session-A").endswith("raven")
    assert resolve_agent_root(agent_dir, "", "session-B").endswith("opensquad_deploy_test")


def test_the_sandbox_root_still_works_without_a_session(tmp_path):
    """The launcher's own read has no session id, and must keep behaving as it always did."""
    agent_dir, raven, _repo = _dirs(tmp_path)
    write_session_cwd(agent_dir, raven)

    assert resolve_agent_root(agent_dir, "", "").endswith("raven")


def test_the_turn_start_check_uses_the_message_that_starts_it():
    """The session id comes from the message, not from whichever session was seen last.

    That was the last hole: the hub remembered the most recent session, so a turn belonging to one
    workspace could be re-rooted by a message that had arrived for another.
    """
    assert "def _apply_cwd_for_message(self, message" in INPUT_HUB
    assert 'sid = str((message or {}).get("session_id") or "").strip()' in INPUT_HUB
    assert INPUT_HUB.count("self._apply_cwd_for_message(") >= 3, "every return path applies it"
    assert "self._check_session_cwd(getattr(self" not in INPUT_HUB
    assert "\n        self._check_session_cwd()\n" not in INPUT_HUB


def test_the_hub_says_which_session_and_origin_it_applied(caplog):
    """The line that turns the next mix-up into a one-minute answer."""
    import logging

    from opensquad.input_hub import input_hub

    original = input_hub._check_session_cwd
    try:
        input_hub._check_session_cwd = lambda sid="": {"path": "C:/w/" + (sid or "shared")}
        with caplog.at_level(logging.INFO, logger="opensquad.input_hub"):
            input_hub._apply_cwd_for_message({"session_id": "sid-A", "content": "hi"})
            input_hub._apply_cwd_for_message({"content": "no sid"})
    finally:
        input_hub._check_session_cwd = original

    logged = [r.getMessage() for r in caplog.records]
    assert any("sid=sid-A" in m and "C:/w/sid-A" in m and "session file" in m for m in logged)
    assert any("sid=(none)" in m and "C:/w/shared" in m and "agent-level" in m for m in logged), logged


def test_the_hub_applies_the_message_session_not_the_remembered_one():
    from opensquad.input_hub import input_hub

    seen: list[str] = []
    original = input_hub._check_session_cwd
    try:
        input_hub._check_session_cwd = lambda sid="": seen.append(str(sid))
        input_hub._apply_cwd_for_message({"session_id": "session-A", "content": "hi"})
        input_hub._apply_cwd_for_message({"content": "no sid"})
    finally:
        input_hub._check_session_cwd = original

    assert seen == ["session-A", ""], "the message's own session, then the legacy path"
