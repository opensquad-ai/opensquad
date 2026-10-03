"""A worker joining a collaboration whose board lives on a paired machine.

Reported by a remote PM: `join_collaboration` returned success twice and recorded nothing anywhere —
its own machine kept no record (`list_active_collaborations` → 0) and the host still showed it as
已邀请, so `assign_task` kept refusing. The join carried only a `collab_id`, and owner resolution
needs either a recorded owner (which only the *creator's* machine writes) or a group; it supplied
neither, so the write ran against the joiner's own empty board and the error was swallowed into a
success.

These tests pin both halves of the fix: the group resolves and records the owner, so the join
forwards to the machine that owns the board; and a join that cannot reach the board reports an
error instead of a success nobody can act on. No test covered this before — every existing join
test ran against a single local board.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad import collab_board as cb  # noqa: E402
from opensquad import (
    peer_bridge,  # noqa: E402
    skill_loader,  # noqa: E402
)
from opensquad.tools import collaboration as collab  # noqa: E402

PEER = "192.168.5.4"
GROUP = "g-remote"
COLLAB = "B26D77"
CARD = "software_dev_team"


@pytest.fixture(autouse=True)
def board(tmp_path, monkeypatch):
    """A board of this machine's own, and the parts of the tool that touch the world."""
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cb, "_board_owners_file", lambda: str(tmp_path / "board_owners.json"))
    monkeypatch.setattr(cb, "active_tasks_for", lambda *a, **k: [])
    monkeypatch.setattr(collab, "_my_agent_id", lambda: "pm")
    monkeypatch.setattr(collab, "_resolve_my_agent_ids", lambda: {"pm"})
    monkeypatch.setattr(skill_loader, "add_skill_from_file", lambda *a, **k: {"success": True})
    monkeypatch.setattr(skill_loader, "get_loaded_skills", lambda: [])
    monkeypatch.setattr(
        "opensquad.input_hub.input_hub",
        SimpleNamespace(agent_dir="C:\\agents\\pm", push=lambda *a, **k: None),
        raising=False,
    )
    return tmp_path


@pytest.fixture
def forwarded(monkeypatch):
    """Capture what would have crossed the wire, instead of crossing it."""
    calls: list[tuple[str, tuple, dict]] = []

    def _remote_call(op, args, kwargs):
        calls.append((op, args, kwargs))
        return {}

    monkeypatch.setattr(cb, "_remote_call", _remote_call)
    # The peer answers at this address; the real lookup needs a config this test does not have.
    monkeypatch.setattr(cb, "_board_hint_base_url", lambda host: f"http://{PEER}:9555" if host else "")
    monkeypatch.setattr(peer_bridge, "load_peers", lambda: {PEER: {"host": PEER, "groups": [GROUP]}})
    return calls


def test_joining_with_the_group_records_the_owner_and_forwards(forwarded):
    """The reported case: the worker knows the group, so the join must reach that machine."""
    result = collab.join_collaboration(card=CARD, collab_id=COLLAB, group_id=GROUP)

    assert result["status"] == "success", result
    # the group resolved the owner, and the id is remembered for every later call
    assert cb.board_owners() == {COLLAB: PEER}
    # …and the membership write went there, not to this machine's empty board
    ops = [op for op, _args, _kwargs in forwarded]
    assert "update_task" in ops and "mark_participant" in ops, ops
    assert cb.list_tasks() == [], "nothing may be written to this machine's own board"


def test_a_join_that_cannot_reach_the_board_is_an_error():
    """No group, no recorded owner: the old code said success. It must say what went wrong."""
    result = collab.join_collaboration(card=CARD, collab_id=COLLAB)

    assert result["status"] == "error", result
    assert result["code"] == "join_tracking_failed"
    assert "group_id" in result["message"], "the message has to name the way out"
    assert cb.board_owners() == {}


def test_a_forwarding_failure_is_reported_not_swallowed(forwarded, monkeypatch):
    """The owner is resolved but refuses or is down — still not a success."""
    monkeypatch.setattr(cb, "_remote_call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("peer refused")))

    result = collab.join_collaboration(card=CARD, collab_id=COLLAB, group_id=GROUP)

    assert result["status"] == "error" and result["code"] == "join_tracking_failed"
    assert "peer refused" in result["message"]


def test_list_active_collaborations_includes_a_remote_board(board, monkeypatch):
    """The join is invisible locally, so the listing has to fetch the remote board by id."""
    cb.remember_board_owner(COLLAB, PEER)
    monkeypatch.setattr(
        cb,
        "get_task",
        lambda task_id="": {"task_id": task_id, "status": "active", "task_name": "秒表 Demo", "members": ["pm"]},
    )

    result = collab.list_active_collaborations()

    assert result["status"] == "success", result
    assert result["count"] == 1
    assert result["collaborations"][0]["task_id"] == COLLAB
    assert result["collaborations"][0]["task_name"] == "秒表 Demo"


def test_an_unreachable_remote_board_is_named_not_fatal(board, monkeypatch):
    """A peer that is down must not make the listing look empty for its own reason."""
    cb.remember_board_owner(COLLAB, PEER)

    def _boom(task_id: str = ""):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(cb, "get_task", _boom)

    result = collab.list_active_collaborations()

    assert result["status"] == "success" and result["count"] == 0
    assert result["unreachable"] == [COLLAB]


def test_the_team_is_told_to_bring_the_group():
    """The fix is useless if nobody is told about it, so the instruction ships with it.

    Four places say how to join: the invitation the worker receives, the message the PM gets when
    assignment is blocked, the skill, and the prompt part.
    """
    collab_src = (_SRC / "opensquad" / "tools" / "collaboration.py").read_text(encoding="utf-8")
    skill = (_SRC / "skills" / "collaboration-workflow" / "SKILL.md").read_text(encoding="utf-8")
    prompt = (_SRC / "prompts" / "parts" / "common_2.12_file_transfer_distribution.md").read_text(encoding="utf-8")

    assert 'group_id="{group_id}"' in collab_src, "the invitation must carry the collaboration's group"
    assert "跨机协作者必须带上 group_id" in collab_src, "and the blocked-assignment hint must say so"
    assert "join_tracking_failed" in collab_src
    assert "group_id" in skill and "group_id" in prompt
