"""Phantom member ids must not hold a dispatch hostage.

A malformed members list recorded '[', '"', 'p', 'm' and ']' as invited members. No such agent can
ever accept, so the collaboration was permanently stuck: assign_task validates the same member
table and refused every assignment. Rebuilding the card was one way out; not treating an id that
cannot be an agent name as a member is the other, and it costs nobody their approvals.

A real member — on this machine or a paired one — is still blocked, which is the point of the
check and is asserted here too.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad import collab_board as cb  # noqa: E402
from opensquad.tools import collaboration as collab  # noqa: E402

COLLAB = "461649"


def _go(monkeypatch, pending):
    monkeypatch.setattr(collab, "_gate_requirement_message", lambda *a, **k: "")
    monkeypatch.setattr(collab, "_not_accepted_message", lambda *a, **k: "not accepted yet")
    monkeypatch.setattr(cb, "get_task", lambda **k: {"extra": {"project_dir": "D:/work/x"}})
    monkeypatch.setattr(cb, "pending_members", lambda collab_id: list(pending))
    monkeypatch.setattr(cb, "active_tasks_for", lambda *a, **k: [{"task_id": "other"}])
    return collab.assign_task(
        collab_id=COLLAB,
        worker_id="pm",
        task_name="do the thing",
        item_key="task_pm_thing",
    )


def test_phantom_ids_do_not_block_dispatch(monkeypatch):
    result = _go(monkeypatch, ["[", '"', "p", "m", "]"])

    # It got past the member gate: the next rule is the one that answers.
    assert result.get("code") == "worker_in_another_task"
    assert result.get("code") != "members_not_accepted"


def test_a_real_member_still_blocks_beside_phantoms(monkeypatch):
    result = _go(monkeypatch, ["[", '"', "p", "m", "]", "pm"])

    assert result.get("code") == "members_not_accepted", "the phantom ids must not hide the real one"


def test_a_real_pending_member_still_blocks(monkeypatch):
    result = _go(monkeypatch, ["pm"])

    assert result.get("code") == "members_not_accepted"


def test_a_hyphenated_real_member_still_blocks(monkeypatch):
    result = _go(monkeypatch, ["agent305-001"])

    assert result.get("code") == "members_not_accepted"
