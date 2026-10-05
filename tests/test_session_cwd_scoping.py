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


def test_a_session_with_no_file_of_its_own_falls_back_to_the_agent_level_one(tmp_path):
    agent_dir, raven, _repo = _dirs(tmp_path)
    write_session_cwd(agent_dir, raven)

    assert read_session_cwd(agent_dir, "session-never-seen")["path"] == str(Path(raven).resolve())
    assert read_session_cwd(agent_dir, "")["path"] == str(Path(raven).resolve())


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


def test_the_turn_start_check_passes_the_session_it_was_serving():
    """The hub's own check ran without an id, so every turn fell back to the shared file — the
    flip-flop. It remembers the session and passes it now; a hub that never saw one keeps the
    historical agent-level behaviour."""
    assert "self.current_session_id = sid" in INPUT_HUB
    assert 'self._check_session_cwd(getattr(self, "current_session_id", ""))' in INPUT_HUB
    assert "\n        self._check_session_cwd()\n" not in INPUT_HUB
