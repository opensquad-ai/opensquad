"""POST /ai-web/collab-board/tasks/{id}/messages — the user's side of a task.

The user typing in the task window must land in the task's own thread and reach
the agents on the task, without ever touching a group.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from opensquad import collab_board as cb

# The gateway backend uses absolute imports rooted at gateway/backend.
_BACKEND_DIR = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from opensquad.gateway.backend.app.ai_web.routes import _main as routes  # noqa: E402


@pytest.fixture()
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    cid = cb.create_task(task_name="发布流程", created_by="pm")["task_id"]
    cb.mark_participant(collab_id=cid, agent_id="coder", state="accepted", name="coder")
    return cid


class _Spy:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send_to_agent(self, agent_id, payload):
        self.sent.append((agent_id, payload))
        return True


@pytest.fixture()
def spy(monkeypatch):
    import app.ai_web.registry as reg

    s = _Spy()
    monkeypatch.setattr(reg, "send_to_agent", s.send_to_agent, raising=False)
    return s


def _user():
    return SimpleNamespace(id="u1", name="老王")


def test_route_is_registered():
    paths = {getattr(r, "path", "") for r in routes.router.routes}
    assert "/api/ai-web/collab-board/tasks/{task_id}/messages" in paths


def test_user_message_lands_in_the_task_thread(board, spy):
    res = asyncio.run(
        routes.post_collab_task_message(task_id=board, body={"content": "请补充验收标准"}, current_user=_user())
    )
    assert res["ok"] is True
    assert res["author"] == "老王"

    items = [i for i in cb.list_items(collab_id=board) if i["item_type"] == "discussion"]
    assert [i["content"] for i in items] == ["请补充验收标准"]
    assert items[0]["agent_id"] == "老王"


def test_agents_are_told_and_asked_to_reply_in_the_task(board, spy):
    asyncio.run(routes.post_collab_task_message(task_id=board, body={"content": "先出方案"}, current_user=_user()))
    assert spy.sent, "the agents on the task must be notified"
    # the creator is a member too, so assert on the agent we put on the task
    notified = [agent_id for agent_id, _ in spy.sent]
    assert "coder" in notified, notified
    text = next(payload for agent_id, payload in spy.sent if agent_id == "coder")["content"]
    assert board in text  # the task id is there, so the reply can route back
    assert "老王" in text
    assert f'collab_id="{board}"' in text
    # and the instruction protects the group
    assert "group" in text.lower()


def test_user_attachments_become_task_attachments(board, spy):
    res = asyncio.run(
        routes.post_collab_task_message(
            task_id=board,
            body={
                "content": "验收材料",
                "attachments": [{"url": "/uploads/spec.pdf", "name": "spec.pdf", "size": "12KB", "type": "file"}],
            },
            current_user=_user(),
        )
    )
    assert res["attachments"] == ["spec.pdf"]
    summary = cb.board_summary(collab_id=board)
    assert [a["name"] for a in summary["attachments"]] == ["spec.pdf"]
    assert [i["content"] for i in summary["items"]["discussion"]] == ["验收材料"]


def test_files_only_message_is_allowed(board, spy):
    res = asyncio.run(
        routes.post_collab_task_message(
            task_id=board,
            body={"attachments": [{"url": "/uploads/shot.png", "name": "shot.png", "type": "image"}]},
            current_user=_user(),
        )
    )
    assert res["ok"] is True
    assert cb.board_summary(collab_id=board)["attachments"][0]["name"] == "shot.png"


def test_empty_message_is_rejected(board, spy):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.post_collab_task_message(task_id=board, body={"content": "  "}, current_user=_user()))
    assert exc.value.status_code == 400


def test_unknown_task_is_404(board, spy):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.post_collab_task_message(task_id="NOPE", body={"content": "hi"}, current_user=_user()))
    assert exc.value.status_code == 404
