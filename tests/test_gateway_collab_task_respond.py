"""POST /groups/{group_id}/collab-tasks/{collab_id}/respond — participant state."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from opensquad import collab_approval as ca
from opensquad import collab_board as cb

# The gateway backend uses absolute imports rooted at gateway/backend (`from app.…`).
_BACKEND_DIR = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from opensquad.gateway.backend.app import api as gw  # noqa: E402


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value or []


class _DB:
    """Returns the queued results in call order; records commits."""

    def __init__(self, results):
        self._results = list(results)
        self.commits = 0

    async def execute(self, *args, **kwargs):
        return _Result(self._results.pop(0))

    async def commit(self):
        self.commits += 1


def _card_message(collab_id: str, participants: list[dict]) -> str:
    payload = ca.build_collab_task_payload(
        collab_id=collab_id, title="发布流程", kind="invite", participants=participants
    )
    return ca.encode_collab_task_message(payload)


@pytest.fixture()
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    return cb


def _respond(collab_id: str, body: dict, db: _DB, name: str = "qa"):
    return asyncio.run(
        gw.respond_collab_task(
            group_id="g-default",
            collab_id=collab_id,
            body=body,
            current_user=SimpleNamespace(id="u1", name=name),
            db=db,
        )
    )


def test_route_is_registered():
    paths = {getattr(r, "path", "") for r in gw.router.routes}
    assert "/groups/{group_id}/collab-tasks/{collab_id}/respond" in paths


def test_accept_marks_participant_and_patches_the_card(board, monkeypatch):
    cid = board.create_task(task_name="发布流程", created_by="pm")["task_id"]
    board.mark_participant(collab_id=cid, agent_id="qa", state="invited", name="QA")

    message = SimpleNamespace(
        id="m1",
        group_id="g-default",
        content=_card_message(cid, [{"agent_id": "qa", "name": "QA", "state": "invited"}]),
        is_edited=False,
    )
    db = _DB([object(), message, message])  # membership, lookup by id, refetch after commit
    broadcast: dict = {}

    async def _notify(group_id, payload):
        broadcast["group_id"] = group_id
        broadcast["payload"] = payload

    monkeypatch.setattr(gw, "notify_message_update", _notify)
    monkeypatch.setattr(
        gw, "format_message_response", lambda m: SimpleNamespace(model_dump=lambda mode="json": {"id": m.id})
    )

    res = _respond(cid, {"action": "accept", "message_id": "m1"}, db)

    assert res["ok"] is True
    assert res["state"] == "accepted"
    assert res["participant"] == "qa"
    assert message.is_edited is True
    # the card was rewritten in place, so a reload shows the new state
    assert ca.parse_collab_task_payload(message.content)["participants"][0]["state"] == "accepted"
    # the board records the same state
    assert board.list_participants(collab_id=cid)[0]["state"] == "accepted"
    assert broadcast["group_id"] == "g-default"
    assert db.commits == 1


def test_decline_records_declined(board, monkeypatch):
    cid = board.create_task(task_name="t", created_by="pm")["task_id"]
    message = SimpleNamespace(id="m1", group_id="g", content=_card_message(cid, []), is_edited=False)
    db = _DB([object(), message, message])
    monkeypatch.setattr(gw, "notify_message_update", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(
        gw, "format_message_response", lambda m: SimpleNamespace(model_dump=lambda mode="json": {"id": m.id})
    )

    res = _respond(cid, {"action": "decline", "message_id": "m1"}, db)
    assert res["state"] == "declined"
    assert board.list_participants(collab_id=cid)[0]["state"] == "declined"
    # an unknown participant is appended to the card rather than dropped
    assert ca.parse_collab_task_payload(message.content)["participants"][0]["agent_id"] == "qa"


def test_an_explicit_participant_overrides_the_responder(board, monkeypatch):
    cid = board.create_task(task_name="t", created_by="pm")["task_id"]
    message = SimpleNamespace(id="m1", group_id="g", content=_card_message(cid, []), is_edited=False)
    # no message_id: the route scans the recent window, so that result is a list
    db = _DB([object(), [message], message])
    monkeypatch.setattr(gw, "notify_message_update", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(
        gw, "format_message_response", lambda m: SimpleNamespace(model_dump=lambda mode="json": {"id": m.id})
    )

    res = _respond(cid, {"action": "accept", "participant_id": "coder-001"}, db)
    assert res["participant"] == "coder-001"
    assert board.list_participants(collab_id=cid)[0]["agent_id"] == "coder-001"


def test_bad_action_is_rejected(board):
    with pytest.raises(HTTPException) as exc:
        _respond("AB12CD", {"action": "maybe"}, _DB([]))
    assert exc.value.status_code == 400


def test_non_member_is_rejected(board):
    with pytest.raises(HTTPException) as exc:
        _respond("AB12CD", {"action": "accept"}, _DB([None]))
    assert exc.value.status_code == 403


def test_missing_card_is_404(board):
    # membership ok, no message by id, the recent-window scan finds nothing
    with pytest.raises(HTTPException) as exc:
        _respond("AB12CD", {"action": "accept", "message_id": "nope"}, _DB([object(), None, []]))
    assert exc.value.status_code == 404


def test_message_without_matching_card_is_400(board):
    cid = board.create_task(task_name="t", created_by="pm")["task_id"]
    other = SimpleNamespace(id="m2", group_id="g", content="just text", is_edited=False)
    with pytest.raises(HTTPException) as exc:
        _respond(cid, {"action": "accept", "message_id": "m2"}, _DB([object(), other, other]))
    assert exc.value.status_code == 400
