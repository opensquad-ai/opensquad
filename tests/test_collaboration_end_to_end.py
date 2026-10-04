"""The collaboration chain end to end, against a real board.

Every call below is the real code path: a collaboration is created, its gates are raised on the
board and approved the way the group card's resolve route approves them, work is dispatched to a
worker, a subtask is moved to done, acceptance is granted and the collaboration is closed. The
refusals along the way are asserted as well — the point of a gate is that it refuses.

Each failure in this sequence has happened for real, one at a time: gates that read as missing
because they were read from the wrong machine, a member table full of phantom ids that blocked
dispatch forever, two workers overwriting each other's subtask. This test is the one that would
have caught them together.

Not covered here: the group-chat and database half (posting the card, the user clicking in the web
UI). Those need a live gateway; the board side is where the field failures were.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad import collab_board as cb  # noqa: E402
from opensquad.tools import collaboration as collab  # noqa: E402

COLLAB = "E2E001"
GROUP = "g-e2e"
CARD = "software_dev_team"


def _workspace(tmp_path, monkeypatch):
    """A board, a card, and a PM — everything the tools reach for, pointed at this test."""
    board = tmp_path / "data" / "collab_board"
    board.mkdir(parents=True, exist_ok=True)
    cards = tmp_path / "collab_cards"
    cards.mkdir(parents=True, exist_ok=True)
    (cards / f"{CARD}.md").write_text(
        "---\nname: software_dev_team\ndescription: test\nsuggested_roles: pm, developer, qa\n---\n\nBody.\n",
        encoding="utf-8",
    )
    pm_dir = tmp_path / "agents" / "pm"
    pm_dir.mkdir(parents=True, exist_ok=True)
    (pm_dir / "config.json").write_text(json.dumps({"agent_id": "pm", "agent_name": "pm"}), encoding="utf-8")

    monkeypatch.setattr(cb, "_board_dir", lambda: str(board))
    monkeypatch.setattr(cb, "_board_owners_file", lambda: str(board / "board_owners.json"))
    monkeypatch.setattr(cb, "board_base_url", lambda **kw: "")
    monkeypatch.setattr(collab, "_collab_cards_dir", lambda: str(cards))
    monkeypatch.setattr(collab, "_agents_dir", lambda: str(tmp_path / "agents"))

    from opensquad.input_hub import input_hub

    monkeypatch.setattr(input_hub, "agent_dir", str(pm_dir), raising=False)
    return board


def _create_task() -> dict:
    return cb.create_task(
        task_id=COLLAB,
        task_name="登录模块",
        created_by="pm",
        group_id=GROUP,
    )


def _raise_gate(step: str) -> str:
    """Raise a gate on the board exactly as request_step_approval writes it."""
    collab.request_step_approval(collab_id=COLLAB, step=step, summary=f"{step} 要点", group_id=GROUP)
    items = cb.list_items(collab_id=COLLAB, visibility="public")
    item = next(
        (
            i
            for i in items
            if str(i.get("item_type")) == "approval"
            and str(((i.get("extra") or {}).get("approval") or {}).get("step")) == step
        ),
        None,
    )
    assert item is not None, f"the gate {step} was never written to the board"
    return str(item.get("item_key") or "")


def _approve(approval_id: str, step: str) -> None:
    """The user clicking 批准: this is the board half of the collab-approvals resolve route."""
    items = cb.list_items(collab_id=COLLAB, visibility="public")
    item = next((i for i in items if str(i.get("item_key")) == approval_id), None)
    assert item is not None
    extra = dict(item.get("extra") or {})
    meta = dict(extra.get("approval") or {})
    meta["status"] = "approved"
    meta["step"] = step
    extra["approval"] = meta
    extra["kind"] = "collab_step_approval"
    cb.upsert_item(
        collab_id=COLLAB,
        agent_id=str(item.get("agent_id") or "pm"),
        item_type="approval",
        item_key=approval_id,
        title=str(item.get("title") or step),
        content=str(item.get("content") or ""),
        status="approved",
        progress=100,
        task_name=str(item.get("task_name") or COLLAB),
        extra=extra,
    )


def test_the_whole_chain_from_start_to_delivery(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch)

    # 1. the collaboration exists, with a project directory the worker can be sent to
    _create_task()
    cb.set_card_and_skills(collab_id=COLLAB, card=CARD, skills=[], project_dir=str(tmp_path / "work"))
    assert cb.gate_states(collab_id=COLLAB) == {
        "确定需求": "missing",
        "讨论方案": "missing",
        "任务分配": "missing",
        "任务验收": "missing",
    }

    # 2. dispatching before the gates are approved is refused, and says which gate and why
    refused = collab.assign_task(collab_id=COLLAB, worker_id="coder", task_name="登录", item_key="task_login")
    assert refused["code"] == "gates_not_approved"
    assert "确定需求" in refused["message"]
    assert "从未发起审批" in refused["message"]

    # 3. the PM raises the first two gates; the user approves them
    first = _raise_gate("确定需求")
    second = _raise_gate("讨论方案")
    assert cb.gate_states(collab_id=COLLAB)["确定需求"] == "pending"

    _approve(first, "确定需求")
    _approve(second, "讨论方案")
    states = cb.gate_states(collab_id=COLLAB)
    assert states["确定需求"] == "approved"
    assert states["讨论方案"] == "approved"

    # 4. work is dispatched, and the member table does not stand in the way
    assigned = collab.assign_task(
        collab_id=COLLAB,
        worker_id="coder",
        task_name="登录模块",
        item_key="task_login",
        subtasks=[{"title": "接口"}, {"title": "页面"}],
    )
    assert assigned["status"] == "success", assigned

    item = next(
        i for i in cb.list_items(collab_id=COLLAB, visibility="public") if str(i.get("item_key")) == "task_login"
    )
    subtasks = (item.get("extra") or {}).get("subtasks") or []
    assert [s["status"] for s in subtasks] == ["pending", "pending"]
    assert all(s.get("id") for s in subtasks), "assign_task must give every subtask an id to move"

    # 5. the worker moves each subtask the way the workflow expects: doing first, then done — the
    #    board refuses pending -> done, which is the point of having a state machine at all
    moved = collab.update_task_progress(
        collab_id=COLLAB, item_key="task_login", subtask_id=subtasks[0]["id"], status="doing", progress=50
    )
    assert moved["status"] == "success", moved
    assert moved["overall_status"] == "doing"

    premature = collab.update_task_progress(
        collab_id=COLLAB, item_key="task_login", subtask_id=subtasks[1]["id"], status="done", progress=100
    )
    assert premature["status"] == "error"
    assert "Invalid status transition" in premature["message"]

    for subtask in subtasks:
        if subtask is not subtasks[0]:
            started = collab.update_task_progress(
                collab_id=COLLAB, item_key="task_login", subtask_id=subtask["id"], status="doing", progress=10
            )
            assert started["status"] == "success", started
        done = collab.update_task_progress(
            collab_id=COLLAB, item_key="task_login", subtask_id=subtask["id"], status="done", progress=100
        )
        assert done["status"] == "success", done

    item = next(
        i for i in cb.list_items(collab_id=COLLAB, visibility="public") if str(i.get("item_key")) == "task_login"
    )
    assert item["status"] == "done"
    assert item["progress"] == 100

    # 6. acceptance is its own gate: closing before it is granted is refused
    refused = collab.end_collaboration(collab_id=COLLAB, card=CARD, group_id=GROUP)
    assert refused["code"] == "gates_not_approved"
    assert "任务验收" in refused["message"]

    acceptance = _raise_gate("任务验收")
    _approve(acceptance, "任务验收")

    # 7. and now it closes
    closed = collab.end_collaboration(collab_id=COLLAB, card=CARD, group_id=GROUP)
    assert closed["status"] == "success", closed

    record = next(t for t in cb.list_tasks(include_stale=True) if str(t.get("task_id")) == COLLAB)
    assert record["status"] in ("done", "archived")
    assert cb.gate_states(collab_id=COLLAB)["任务验收"] == "approved"
