"""Two agent processes updating different subtasks of the same item must both survive.

Each agent is its own process, so a thread lock around the read-modify-write sequence only ever
serialised the threads of one process. Two agents could read the same item, change different
subtasks, and the later write silently dropped the earlier one. The sequence now runs under the
board's file lock, which every writer on the machine shares.

Both cases below are made deterministic rather than racing: each child reads, announces that it has
read, waits (briefly, so the locked case cannot deadlock) for the other to have read too, and only
then writes. Under the lock the second read happens after the first write, so nothing is lost; with
the read outside the lock — how the old code behaved — the second write overwrites from a stale
copy and one update disappears.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

COLLAB = "ROUND1"
ITEM_KEY = "task_x"

WORKER = textwrap.dedent(
    """
    import os, sys, time
    sys.path.insert(0, os.environ["REPO_SRC"])
    from opensquad import collab_board as cb

    marker, guard, read_dir, wait_for = sys.argv[1], sys.argv[2] == "locked", sys.argv[3], sys.argv[4]

    def body(items):
        target = next((i for i in items if str(i.get("item_key", "")) == "task_x"), None)
        extra = target.get("extra") or {}
        notes = list(extra.get("notes") or [])
        open(os.path.join(read_dir, marker), "w").close()
        waited = time.time()
        while time.time() - waited < 2 and not os.path.exists(os.path.join(read_dir, wait_for)):
            time.sleep(0.02)
        notes.append(marker)
        extra["notes"] = notes
        cb.upsert_item(
            collab_id="ROUND1", agent_id="worker", item_type="task", item_key="task_x",
            title="t", content="c", task_name="t", extra=extra,
        )
        return True

    if guard:
        with cb.board_write_lock():
            body(cb._read_items())
    else:
        body(cb._read_items())
    """
)


def _board(tmp_path: Path) -> Path:
    return tmp_path / "data" / "collab_board"


def _seed(tmp_path: Path) -> None:
    board = _board(tmp_path)
    board.mkdir(parents=True, exist_ok=True)
    (board / "board_tasks.json").write_text(
        json.dumps([{"task_id": COLLAB, "task_name": "t", "status": "active", "board_rev": 0}]),
        encoding="utf-8",
    )
    (board / "board_items.json").write_text(
        json.dumps(
            [
                {
                    "id": f"{COLLAB}:worker:task:{ITEM_KEY}",
                    "collab_id": COLLAB,
                    "task_id": COLLAB,
                    "agent_id": "worker",
                    "item_type": "task",
                    "item_key": ITEM_KEY,
                    "title": "t",
                    "content": "c",
                    "status": "doing",
                    "progress": 0,
                    "visibility": "public",
                    "extra": {"subtasks": [{"id": "st_a", "title": "A", "status": "pending"}]},
                }
            ]
        ),
        encoding="utf-8",
    )


def _run_pair(tmp_path: Path, guard: bool) -> list[str]:
    read_dir = tmp_path / f"read-{int(guard)}"
    read_dir.mkdir(parents=True, exist_ok=True)
    # Both children share this temporary workspace, so they contend with each other on one board —
    # and with nothing else, because the lock is now keyed by the board directory rather than being
    # one machine-wide resource shared with the stack serving users.
    env = dict(
        os.environ,
        REPO_SRC=str(_SRC),
        OPENSQUAD_WORKSPACE=str(tmp_path),
    )
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                WORKER,
                marker,
                "locked" if guard else "plain",
                str(read_dir),
                other,
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for marker, other in (("a", "b"), ("b", "a"))
    ]
    for proc in procs:
        _, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err.decode("utf-8", "replace")

    board = json.loads((_board(tmp_path) / "board_items.json").read_text(encoding="utf-8"))
    item = next(i for i in board if str(i.get("item_key")) == ITEM_KEY)
    return sorted((item.get("extra") or {}).get("notes") or [])


def test_both_processes_keep_their_update(tmp_path):
    _seed(tmp_path)

    assert _run_pair(tmp_path, guard=True) == ["a", "b"]


def test_without_the_lock_one_update_is_lost(tmp_path):
    """The old shape — read outside the lock — is what the file lock replaced."""
    _seed(tmp_path)

    assert _run_pair(tmp_path, guard=False) != ["a", "b"], "the window this fix closes is real"
