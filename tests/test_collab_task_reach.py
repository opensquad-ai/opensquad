"""Who a task reaches, and which skills it declares.

Both were wrong in the field, and both are visible in the task window:

* it listed 19 skills — every skill the agent happened to have loaded — instead of the
  ones the project lead chose to run the task with;
* the user's messages in the window never reached the agent that was doing the work: it
  was not in the task's `members`/`participants`, so the notify list skipped it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
_BACKEND_DIR = _SRC / "opensquad" / "gateway" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import opensquad.collab_board as cb  # noqa: E402
import opensquad.skill_loader as skill_loader  # noqa: E402
from opensquad.tools import collaboration as collab_tool  # noqa: E402


@pytest.fixture()
def card_dir(monkeypatch):
    """Card lookup points at the shipped cards (no workspace needed)."""
    real = _SRC / "collab_cards"
    monkeypatch.setattr(collab_tool, "_collab_cards_dir", lambda: str(real))
    return real


def test_a_task_declares_the_chosen_skills_not_every_loaded_one(card_dir, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(skill_loader, "add_skill_from_file", lambda *a, **k: {"success": True})
    # whatever this agent has loaded must not leak into the task's declaration
    monkeypatch.setattr(
        skill_loader,
        "get_loaded_skills",
        lambda: [SimpleNamespace(name="playwright"), SimpleNamespace(name="_smoke_skill")],
    )
    monkeypatch.setattr(cb, "create_task", lambda **k: {"task_id": "T1"})
    monkeypatch.setattr(cb, "board_owner", lambda **k: "")
    monkeypatch.setattr(cb, "remember_board_owner", lambda *a, **k: None)
    monkeypatch.setattr(cb, "set_card_and_skills", lambda **k: captured.update(k) or {})
    monkeypatch.setattr(cb, "mark_participant", lambda **k: {})

    collab_tool.start_collaboration("software_dev_team", skills=["playwright"])

    assert captured["card"] == "software_dev_team"
    # the card itself, plus exactly what the PM named — nothing else
    assert captured["skills"] == ["collab_software_dev_team", "playwright"]
    assert "_smoke_skill" not in captured["skills"]


def test_without_a_choice_only_the_card_is_declared(card_dir, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(skill_loader, "add_skill_from_file", lambda *a, **k: {"success": True})
    monkeypatch.setattr(skill_loader, "get_loaded_skills", lambda: [SimpleNamespace(name="playwright")])
    monkeypatch.setattr(cb, "create_task", lambda **k: {"task_id": "T2"})
    monkeypatch.setattr(cb, "board_owner", lambda **k: "")
    monkeypatch.setattr(cb, "remember_board_owner", lambda *a, **k: None)
    monkeypatch.setattr(cb, "set_card_and_skills", lambda **k: captured.update(k) or {})
    monkeypatch.setattr(cb, "mark_participant", lambda **k: {})

    collab_tool.start_collaboration("software_dev_team")

    assert captured["skills"] == ["collab_software_dev_team"]


def test_the_users_message_reaches_the_agent_working_on_the_board(monkeypatch):
    """Regression: the notify list was members + participants only; the agent that had
    picked up work (and reported progress on the board) never heard the user."""
    import app.ai_web.registry as agent_registry
    import app.relay as gw_relay
    from app.ai_web.routes import _main as routes

    task = {
        "task_id": "T1",
        "task_name": "贪吃蛇游戏",
        "created_by": "pm",
        "members": ["pm"],
        "extra": {"group_id": "g-1"},
    }

    def _local_call(op, **kwargs):
        return {
            "get_task": task,
            "list_participants": [],
            "append_public_discussion": {"id": "i1"},
            "attach_files": {},
            "board_summary": {"items": {"progress": [{"agent_id": "agent305"}]}},
        }.get(op, {})

    monkeypatch.setattr(cb, "local_call", _local_call)

    sent: list[tuple] = []

    async def _send(agent_id, message):
        sent.append((agent_id, message))
        return True

    monkeypatch.setattr(agent_registry, "send_to_agent", _send, raising=False)

    relayed: list[dict] = []

    async def _fan(group_id, chat, origin_host=""):
        relayed.append({"group_id": group_id, "chat": chat})
        return {"ok": True}

    monkeypatch.setattr(gw_relay, "fan_out_task", _fan, raising=False)

    user = SimpleNamespace(id="u-1", name="ss")
    res = asyncio.run(routes.post_collab_task_message("T1", {"content": "这里能看到吗"}, user))

    assert "agent305" in res["notified"]
    assert [agent for agent, _ in sent] == ["pm", "agent305"]
    # each recipient gets a frame addressed to *it*: a strict-mode agent discards
    # anything without its own mention, which is how the user's words went unread
    assert sent[1][1]["wake"] is True
    assert "agent305" in sent[1][1]["mentions"]
    assert sent[1][1]["channel"] == "task"
    # and the same payload crosses to a paired machine's agent
    assert relayed and relayed[0]["group_id"] == "g-1"
    assert "这里能看到吗" in relayed[0]["chat"]["content"]
