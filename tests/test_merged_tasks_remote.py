"""A task that lives on a paired machine must show up in a list that claims to be complete.

The strip above the composer, and the agent tool that lists collaborations, both used to read only
the local board file. A collaboration created on the other machine was simply absent, so the list
said "nothing running" while the peer was mid-task — the same shape of failure as a confident zero
elsewhere in this project.

`merged_tasks` reads the local board, then reads every collaboration recorded in
board_owners.json by id, which carries the hint that routes the call to its owner. An unreachable
owner contributes nothing and is named, never silently dropped.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad import collab_board as cb  # noqa: E402


def _task(task_id: str, status: str = "active", name: str = "") -> dict:
    return {
        "task_id": task_id,
        "task_name": name or f"task {task_id}",
        "status": status,
        "members": [],
        "created_at": "2026-10-04T00:00:00Z",
        "updated_at": "2026-10-04T00:00:00Z",
    }


def _wire(monkeypatch, local, owners, remote):
    monkeypatch.setattr(cb, "list_tasks", lambda include_stale=False: list(local))
    monkeypatch.setattr(cb, "board_owners", lambda: dict(owners))

    def _get_task(task_id: str):
        value = remote.get(task_id)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(cb, "get_task", _get_task)


def test_a_task_on_a_paired_machine_is_included(monkeypatch):
    _wire(
        monkeypatch,
        local=[_task("LOCAL1")],
        owners={"REMOTE": "peer-1"},
        remote={"REMOTE": _task("REMOTE", name="on the other machine")},
    )

    tasks, unreachable = cb.merged_tasks()

    assert sorted(t["task_id"] for t in tasks) == ["LOCAL1", "REMOTE"]
    assert unreachable == []


def test_an_unreachable_owner_is_named_not_dropped(monkeypatch):
    _wire(
        monkeypatch,
        local=[_task("LOCAL1")],
        owners={"REMOTE": "peer-1", "GONE": "peer-2"},
        remote={"REMOTE": _task("REMOTE"), "GONE": ConnectionError("peer is down")},
    )

    tasks, unreachable = cb.merged_tasks()

    assert [t["task_id"] for t in tasks] == ["LOCAL1", "REMOTE"]
    assert unreachable == ["GONE"]


def test_a_task_already_local_is_not_read_twice(monkeypatch):
    calls = []

    def _get_task(task_id: str):
        calls.append(task_id)
        return _task(task_id)

    monkeypatch.setattr(cb, "list_tasks", lambda include_stale=False: [_task("BOTH")])
    monkeypatch.setattr(cb, "board_owners", lambda: {"BOTH": "peer-1"})
    monkeypatch.setattr(cb, "get_task", _get_task)

    tasks, _unreachable = cb.merged_tasks()

    assert [t["task_id"] for t in tasks] == ["BOTH"]
    assert calls == []


def test_an_owner_answering_with_nothing_is_not_counted(monkeypatch):
    _wire(monkeypatch, local=[], owners={"EMPTY": "peer-1"}, remote={"EMPTY": {}})

    tasks, unreachable = cb.merged_tasks()

    assert tasks == []
    assert unreachable == []


def test_a_broken_owner_record_does_not_break_the_list(monkeypatch):
    _wire(monkeypatch, local=[_task("LOCAL1")], owners={}, remote={})

    def _boom():
        raise RuntimeError("board_owners.json is unreadable")

    monkeypatch.setattr(cb, "board_owners", _boom)

    tasks, unreachable = cb.merged_tasks()

    assert [t["task_id"] for t in tasks] == ["LOCAL1"]
    assert unreachable == []


def test_the_board_route_uses_the_merged_list():
    src = (Path(__file__).resolve().parents[1] / "src/opensquad/gateway/backend/app/ai_web/routes/_main.py").read_text(
        encoding="utf-8"
    )

    start = src.index('@router.get("/collab-board/tasks")')
    body = src[start : src.index("@router.post", start)]

    assert "collab_board.merged_tasks()" in body, "the strip reads this route; it must be complete"
