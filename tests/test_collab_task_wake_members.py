"""An agent's task-window message wakes the other members.

The user's task messages were made live (directed, wake-worthy frames); an *agent's*
message only landed on the board, so teammates found it by polling — the same gap, one
author over. Discussion messages (and progress that names someone) now wake the members,
while plain progress pings stay on the board so they do not wake everyone per tick.
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
from opensquad.system_config import syscfg  # noqa: E402
from opensquad.tools import collaboration as collab_tool  # noqa: E402


@pytest.fixture()
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(syscfg, "workspace_data_dir", lambda *parts: str(tmp_path / "_".join(parts)))
    agent_dir = tmp_path / "agents" / "coder"
    agent_dir.mkdir(parents=True)
    monkeypatch.setattr(input_hub_mod.input_hub, "agent_dir", str(agent_dir))
    task = cb.create_task(task_name="五子棋", created_by="pm", group_id="g-1")
    cb.mark_participant(collab_id=task["task_id"], agent_id="agent305", state="accepted", name="Agent305")
    monkeypatch.setattr(peer_bridge_mod, "owner_bridge", lambda **k: (None, "no bridge"))
    monkeypatch.setattr(bridge_mod, "gateway_base_url", lambda: "http://127.0.0.1:9555")
    return task["task_id"]


@pytest.fixture()
def posted(monkeypatch):
    calls: list[dict] = []

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"ok": True, "notified": []}

    class _Requests:
        @staticmethod
        def post(url, **kwargs):
            calls.append({"url": url, "json": kwargs.get("json") or {}})
            return _Resp()

    monkeypatch.setitem(sys.modules, "requests", _Requests)
    return calls


def test_a_discussion_message_wakes_the_other_members(board, posted):
    res = collab_tool.post_task_message(board, "方案我看过了，按这个做")

    assert res["status"] == "success"
    assert len(posted) == 1
    assert posted[0]["url"].endswith("/api/ai-web/collab/invite")
    assert sorted(posted[0]["json"]["agents"]) == ["agent305", "pm"]  # everyone but the author
    assert "@coder:" in posted[0]["json"]["message"]


def test_the_author_is_not_woken_by_its_own_message(board, posted):
    collab_tool.post_task_message(board, "自言自语")

    assert "coder" not in posted[0]["json"]["agents"]


def test_a_progress_ping_stays_on_the_board(board, posted):
    """Progress updates are status, not conversation: waking every member per tick is
    noise."""
    collab_tool.post_task_message(board, "完成 60%", kind="progress")

    assert posted == []


def test_a_progress_ping_that_names_someone_does_wake_them(board, posted):
    collab_tool.post_task_message(board, "@agent305 卡在渲染上，帮忙看看", kind="progress")

    assert len(posted) == 1
    assert "agent305" in posted[0]["json"]["agents"]
