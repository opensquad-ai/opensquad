"""One collaboration at a time — enforced at every door in.

An agent in two live tasks reports progress on the wrong board, answers the wrong
thread, and starts collaborations it cannot finish. Creating, joining and being
assigned all refuse while another task is live, and the refusal says how to get out.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import opensquad.collab_board as cb  # noqa: E402
import opensquad.input_hub as input_hub_mod  # noqa: E402
import opensquad.skill_loader as skill_loader  # noqa: E402
from opensquad.system_config import syscfg  # noqa: E402
from opensquad.tools import collaboration as collab_tool  # noqa: E402


@pytest.fixture()
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(syscfg, "workspace_data_dir", lambda *parts: str(tmp_path / "_".join(parts)))
    agent_dir = tmp_path / "agents" / "pm"
    agent_dir.mkdir(parents=True)
    monkeypatch.setattr(input_hub_mod.input_hub, "agent_dir", str(agent_dir))
    monkeypatch.setattr(collab_tool, "_collab_cards_dir", lambda: str(_SRC / "collab_cards"))
    monkeypatch.setattr(skill_loader, "add_skill_from_file", lambda *a, **k: {"success": True})
    return tmp_path


def test_active_tasks_for_sees_the_task_an_agent_is_in(board):
    task = cb.create_task(task_name="贪吃蛇游戏", created_by="pm", group_id="g-1")
    cb.mark_participant(collab_id=task["task_id"], agent_id="coder", state="invited")

    assert [t["task_id"] for t in cb.active_tasks_for("pm")] == [task["task_id"]]
    assert cb.active_tasks_for("pm")[0]["role"] == "creator"
    assert [t["role"] for t in cb.active_tasks_for("coder")] == ["member"]
    assert cb.active_tasks_for("stranger") == []


def test_a_finished_task_stops_counting(board):
    task = cb.create_task(task_name="贪吃蛇游戏", created_by="pm", group_id="g-1")

    cb.update_task(task_id=task["task_id"], status="done")

    assert cb.active_tasks_for("pm") == []


def test_start_collaboration_refuses_a_second_live_task(board):
    cb.create_task(task_name="贪吃蛇游戏", created_by="pm", group_id="g-1")

    res = collab_tool.start_collaboration("software_dev_team")

    assert res["status"] == "error"
    assert res["code"] == "already_in_task"
    # and it says how to get out of the refusal
    assert "end_collaboration" in res["message"]


def test_join_collaboration_refuses_another_task_but_allows_its_own(board):
    mine = cb.create_task(task_name="贪吃蛇游戏", created_by="someone-else", group_id="g-1")
    cb.mark_participant(collab_id=mine["task_id"], agent_id="pm", state="accepted")
    other = cb.create_task(task_name="另一个任务", created_by="someone-else", group_id="g-1")

    refused = collab_tool.join_collaboration("software_dev_team", collab_id=other["task_id"])
    assert refused["status"] == "error" and refused["code"] == "already_in_task"

    # re-joining the task it is already in is a retry, not a second task
    again = collab_tool.join_collaboration("software_dev_team", collab_id=mine["task_id"])
    assert again.get("code") != "already_in_task"


def test_assign_task_refuses_a_worker_that_is_in_another_live_task(board):
    for step in ("确定需求", "讨论方案"):
        cb.upsert_item(
            collab_id="T1",
            task_name="贪吃蛇游戏",
            agent_id="pm",
            item_type="approval",
            item_key=f"ap_{step}",
            title=step,
            content=step,
            status="approved",
            extra={"approval": {"step": step}},
        )
    cb.create_task(task_name="贪吃蛇游戏", task_id="T1", created_by="pm", group_id="g-1")
    cb.mark_participant(collab_id="T1", agent_id="coder", state="accepted")
    cb.create_task(task_name="另一个任务", task_id="T2", created_by="other", group_id="g-1")
    cb.mark_participant(collab_id="T2", agent_id="coder", state="accepted")

    res = collab_tool.assign_task(collab_id="T1", worker_id="coder", task_name="写游戏")

    assert res["status"] == "error"
    assert res["code"] == "worker_in_another_task"
    assert "T2" in res["message"]
