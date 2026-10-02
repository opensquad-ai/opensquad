"""The collaboration gates and invitations are enforced, not just drawn.

Two field reports: an invitee that never accepted was given work anyway, and a task ran
to completion with all four gates still 未开始. Both are refused now — with the steps that
unblock them, because an error an agent cannot act on is just a wall.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import opensquad.collab_board as cb  # noqa: E402
from opensquad.system_config import syscfg  # noqa: E402
from opensquad.tools import collaboration as collab_tool  # noqa: E402


@pytest.fixture()
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(syscfg, "workspace_data_dir", lambda *parts: str(tmp_path / "_".join(parts)))
    task = cb.create_task(task_name="贪吃蛇游戏", created_by="pm", group_id="g-1")
    return task["task_id"]


def _approve(collab_id: str, step: str) -> None:
    cb.upsert_item(
        collab_id=collab_id,
        task_name="贪吃蛇游戏",
        agent_id="pm",
        item_type="approval",
        item_key=f"ap_{step}",
        title=step,
        content=step,
        status="approved",
        extra={"approval": {"step": step}},
    )


def test_gates_start_missing_and_flip_when_the_user_approves(board):
    assert cb.gate_states(board)["确定需求"] == "missing"

    _approve(board, "确定需求")

    states = cb.gate_states(board)
    assert states["确定需求"] == "approved"
    assert states["讨论方案"] == "missing"


def test_assign_task_refuses_until_the_first_two_gates_are_approved(board):
    cb.mark_participant(collab_id=board, agent_id="coder", state="accepted")

    res = collab_tool.assign_task(collab_id=board, worker_id="coder", task_name="写游戏")

    assert res["status"] == "error"
    assert res["code"] == "gates_not_approved"
    # the error has to be actionable: which gate, and how to get it approved
    assert "确定需求" in res["message"]
    assert "request_step_approval" in res["message"]
    assert "批准" in res["message"]


def test_assign_task_refuses_while_a_member_has_not_accepted(board):
    """Work is handed out to a team that has assembled: one invitee still at 已邀请 blocks
    every assignment, not just the one aimed at that invitee."""
    _approve(board, "确定需求")
    _approve(board, "讨论方案")
    cb.set_card_and_skills(collab_id=board, project_dir="D:/work/snake")
    cb.mark_participant(collab_id=board, agent_id="coder", state="invited")
    cb.mark_participant(collab_id=board, agent_id="qa", state="accepted")

    res = collab_tool.assign_task(collab_id=board, worker_id="qa", task_name="写游戏")

    assert res["status"] == "error"
    assert res["code"] == "members_not_accepted"
    assert "coder" in res["message"]
    assert "join_collaboration" in res["message"]
    assert "invited" in res["message"]


def test_pending_members_lists_invited_declined_and_unrecorded(board):
    cb.mark_participant(collab_id=board, agent_id="coder", state="invited")
    cb.mark_participant(collab_id=board, agent_id="qa", state="declined")
    cb.mark_participant(collab_id=board, agent_id="ops", state="accepted")
    cb.update_task(task_id=board, add_member="newbie")  # listed on the task, never invited

    pending = {p["agent_id"]: p["state"] for p in cb.pending_members(board)}

    assert pending == {"coder": "invited", "qa": "declined", "newbie": "not_invited"}
    assert "ops" not in pending  # accepted
    assert "pm" not in pending  # the creator is in by definition


def test_assign_task_proceeds_once_the_gates_pass_and_the_worker_accepted(board):
    _approve(board, "确定需求")
    _approve(board, "讨论方案")
    cb.set_card_and_skills(collab_id=board, project_dir="D:/work/snake")
    cb.mark_participant(collab_id=board, agent_id="coder", state="accepted")

    res = collab_tool.assign_task(collab_id=board, worker_id="coder", task_name="写游戏")

    assert res.get("status") == "success", res
    assert cb.accepted_members(board) == {"pm", "coder"}


def test_assign_task_refuses_until_the_project_dir_is_recorded(board):
    """The window tells every worker where the project lives; without the directory each of
    them picks one of its own, and a machine on the other end guesses too."""
    _approve(board, "确定需求")
    _approve(board, "讨论方案")
    cb.mark_participant(collab_id=board, agent_id="coder", state="accepted")

    res = collab_tool.assign_task(collab_id=board, worker_id="coder", task_name="写游戏")

    assert res["status"] == "error"
    assert res["code"] == "project_dir_missing"
    assert "set_project_dir" in res["message"]
    assert board in res["message"]


def test_set_project_dir_records_it_and_the_summary_exposes_it(board):
    res = collab_tool.set_project_dir(collab_id=board, project_dir=" D:/work/snake ")

    assert res["status"] == "success"
    assert res["project_dir"] == "D:/work/snake"
    assert cb.board_summary(collab_id=board)["project_dir"] == "D:/work/snake"

    # …and only then does assignment get past that gate
    _approve(board, "确定需求")
    _approve(board, "讨论方案")
    cb.mark_participant(collab_id=board, agent_id="coder", state="accepted")
    assert collab_tool.assign_task(collab_id=board, worker_id="coder", task_name="写游戏").get("status") == "success"


def test_set_project_dir_refuses_an_empty_path_or_an_unknown_task(board):
    empty = collab_tool.set_project_dir(collab_id=board, project_dir="   ")

    assert empty["status"] == "error"
    assert empty["code"] == "project_dir_missing"
    assert cb.board_summary(collab_id=board)["project_dir"] == ""

    missing = collab_tool.set_project_dir(collab_id="NOPE00", project_dir="D:/work/x")

    assert missing["status"] == "error"
    assert "not found" in missing["message"]


def test_end_collaboration_refuses_without_the_acceptance_gate(board):
    res = collab_tool.end_collaboration("software_dev_team", collab_id=board)

    assert res["status"] == "error"
    assert res["code"] == "gates_not_approved"
    assert "任务验收" in res["message"]
