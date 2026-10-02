"""Ending a task is announced in the task window — the group gets nothing.

The closure used to be a group announcement plus a "done" card, which turned the group
into a log of task bookkeeping. It is task talk: it belongs in the window, with the members
woken there so they know to unload the card.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import opensquad.bridge as bridge_mod  # noqa: E402
import opensquad.collab_board as cb  # noqa: E402
import opensquad.input_hub as input_hub_mod  # noqa: E402
import opensquad.peer_bridge as peer_bridge_mod  # noqa: E402
import opensquad.skill_loader as skill_loader  # noqa: E402
from opensquad.system_config import syscfg  # noqa: E402
from opensquad.tools import collaboration as collab_tool  # noqa: E402


@pytest.fixture()
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(syscfg, "workspace_data_dir", lambda *parts: str(tmp_path / "_".join(parts)))
    agent_dir = tmp_path / "agents" / "coder"
    agent_dir.mkdir(parents=True)
    monkeypatch.setattr(input_hub_mod.input_hub, "agent_dir", str(agent_dir))
    monkeypatch.setattr(skill_loader, "remove_skill", lambda *a, **k: {"success": True})
    monkeypatch.setattr(bridge_mod, "gateway_base_url", lambda: "http://127.0.0.1:9555")

    task = cb.create_task(task_name="五子棋", created_by="coder", group_id="g-1")
    cb.mark_participant(collab_id=task["task_id"], agent_id="agent305", state="accepted", name="Agent305")
    cb.upsert_item(
        collab_id=task["task_id"],
        task_name="五子棋",
        agent_id="coder",
        item_type="approval",
        item_key="ap_acceptance",
        title="任务验收",
        content="任务验收",
        status="approved",
        extra={"approval": {"step": "任务验收"}},
    )
    return task["task_id"]


@pytest.fixture()
def posted(monkeypatch):
    calls: list[dict] = []

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"ok": True}

    class _Requests:
        @staticmethod
        def post(url, **kwargs):
            calls.append({"url": url, "json": kwargs.get("json") or {}})
            return _Resp()

    monkeypatch.setitem(sys.modules, "requests", _Requests)
    return calls


@pytest.fixture()
def group_bridge(monkeypatch):
    """A bridge the code *could* post to the group with; records any attempt.

    `owner_bridge` itself is consulted by the directed delivery (it resolves which gateway
    owns the group), so the invariant is not "never called" but "never sent to the group".
    """
    sent: list[tuple] = []

    class _Bridge:
        base_url = "http://127.0.0.1:9555"

        def send_message(self, msg, target_id="", target_type="group"):
            sent.append((target_id, str(msg)[:80]))
            return True

        def list_groups_api(self):
            return []

        def get_group_detail_api(self, target):
            return {}

    monkeypatch.setattr(peer_bridge_mod, "owner_bridge", lambda **kwargs: (_Bridge(), ""))
    return sent


def test_the_closure_lands_in_the_task_thread_and_wakes_the_members(board, posted, group_bridge):
    res = collab_tool.end_collaboration("software_dev_team", collab_id=board, group_id="g-1")

    assert res["status"] == "success"
    assert "task window" in str(res["notification"])

    # the window keeps the record: a discussion item titled 协作结束
    items = [i for i in cb._read_items() if str(i.get("collab_id")) == board]
    closure = [i for i in items if str(i.get("title")) == "协作结束"]
    assert len(closure) == 1
    assert "已结束" in str(closure[0].get("content"))

    # members are woken (directed delivery), the author is not
    assert posted and posted[0]["url"].endswith("/api/ai-web/collab/invite")
    assert posted[0]["json"]["agents"] == ["agent305"]


def test_nothing_is_posted_to_the_group(board, posted, group_bridge):
    collab_tool.end_collaboration("software_dev_team", collab_id=board, group_id="g-1")

    # the notice never goes to the group — only into the thread (and to the members)
    assert group_bridge == []
    assert posted[0]["url"].endswith("/api/ai-web/collab/invite")


def test_the_task_is_closed_so_the_agent_can_start_another(board, posted, group_bridge):
    collab_tool.end_collaboration("software_dev_team", collab_id=board, group_id="g-1")

    assert cb.active_tasks_for("coder") == []
