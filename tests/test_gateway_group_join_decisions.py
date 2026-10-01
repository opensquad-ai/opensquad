"""Reviewing private-group join requests: listing them and deciding them.

The owner's half of the flow that `POST /groups/{id}/join-request` starts.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from opensquad.gateway.backend.app import api as gw  # noqa: E402


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value or []


class _DB:
    def __init__(self, results):
        self._results = list(results)
        self.statements: list = []
        self.commits = 0

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(str(statement))
        return _Result(self._results.pop(0))

    async def commit(self):
        self.commits += 1


def _handler(suffix: str, method: str = "GET"):
    for route in gw.router.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if path.endswith(suffix) and method in methods:
            return route.endpoint
    raise AssertionError(f"{method} {suffix} not found")


def _group(created_by="1", is_private=True):
    return SimpleNamespace(id="g-private", name="secret", created_by=created_by, is_private=is_private)


def _request(status="pending", user_id="7"):
    return SimpleNamespace(
        id="jr_abc",
        group_id="g-private",
        user_id=user_id,
        message="B 机 coder",
        status=status,
        created_at=None,
        decided_at=None,
        decided_by=None,
    )


USER = SimpleNamespace(id="1", name="owner")


# ── listing ────────────────────────────────────────────────────────────────


def test_only_members_may_list_requests():
    db = _DB([_group(), None])  # group, membership

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_handler("/join-requests")(group_id="g-private", current_user=USER, db=db))

    assert exc.value.status_code == 403
    assert "members" in exc.value.detail


def test_an_unknown_group_cannot_be_listed():
    db = _DB([None])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_handler("/join-requests")(group_id="g-private", current_user=USER, db=db))

    assert exc.value.status_code == 404


def test_a_member_sees_the_pending_asks():
    db = _DB([_group(), object(), [_request(), _request(user_id="8")]])

    res = asyncio.run(_handler("/join-requests")(group_id="g-private", current_user=USER, db=db))

    assert res["count"] == 2
    assert res["requests"][0]["user_id"] == "7"
    assert res["requests"][0]["message"] == "B 机 coder"


# ── deciding ───────────────────────────────────────────────────────────────


def _decide(db, body, current_user=USER):
    return asyncio.run(
        _handler("/join-requests/{request_id}", "POST")(
            group_id="g-private", request_id="jr_abc", body=body, current_user=current_user, db=db
        )
    )


def test_an_invalid_action_is_rejected():
    with pytest.raises(HTTPException) as exc:
        _decide(_DB([]), {"action": "maybe"})

    assert exc.value.status_code == 400


def test_only_the_owner_decides():
    db = _DB([_group(created_by="2")])

    with pytest.raises(HTTPException) as exc:
        _decide(db, {"action": "approve"})

    assert exc.value.status_code == 403
    assert "owner" in exc.value.detail


def test_approving_writes_the_membership():
    # group, request, not-yet-member, then the membership INSERT itself
    db = _DB([_group(), _request(), None, None])

    res = _decide(db, {"action": "approve"})

    assert res["status"] == "approved"
    assert res["user_id"] == "7"
    assert db.commits == 1
    # the insert happened (the last statement is the membership write)
    assert "INSERT INTO group_members" in db.statements[-1].upper().replace("GROUP_MEMBERS", "group_members").upper()


def test_rejecting_does_not_touch_membership():
    db = _DB([_group(), _request()])

    res = _decide(db, {"action": "reject"})

    assert res["status"] == "rejected"
    assert db.commits == 1
    assert all("insert" not in s.lower() or "group_members" not in s.lower() for s in db.statements)


def test_an_already_decided_request_is_a_conflict():
    db = _DB([_group(), _request(status="approved")])

    with pytest.raises(HTTPException) as exc:
        _decide(db, {"action": "approve"})

    assert exc.value.status_code == 409


def test_an_unknown_request_is_404():
    db = _DB([_group(), None])

    with pytest.raises(HTTPException) as exc:
        _decide(db, {"action": "approve"})

    assert exc.value.status_code == 404
