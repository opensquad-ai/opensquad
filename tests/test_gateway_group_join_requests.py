"""POST /groups/{id}/join-request — the way into a private group.

Public groups need no approval (join directly), and a private group used to be a
hard 403 with no path at all, which left an agent on another machine unable to
take part. This endpoint records the ask for the owner to decide on.
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


class _DB:
    def __init__(self, results):
        self._results = list(results)
        self.added: list = []
        self.commits = 0

    async def execute(self, *args, **kwargs):
        return _Result(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _handler():
    for route in gw.router.routes:
        if getattr(route, "path", "").endswith("/groups/{group_id}/join-request") and "POST" in (
            getattr(route, "methods", set()) or set()
        ):
            return route.endpoint
    raise AssertionError("POST /groups/{group_id}/join-request not found")


def _call(db, *, group, user_id="7", body=None):
    return asyncio.run(
        _handler()(
            group_id="g-private",
            body=body or {},
            current_user=SimpleNamespace(id=user_id, name="coder-001"),
            db=db,
        )
    )


def _group(is_private=True):
    return SimpleNamespace(id="g-private", name="secret", is_private=is_private)


def test_a_public_group_needs_no_request():
    db = _DB([_group(is_private=False)])

    with pytest.raises(HTTPException) as exc:
        _call(db, group=_group(is_private=False))

    assert exc.value.status_code == 400
    assert "public" in exc.value.detail
    assert db.added == []


def test_a_member_does_not_ask():
    db = _DB([_group(), object()])  # group, membership

    with pytest.raises(HTTPException) as exc:
        _call(db, group=_group())

    assert exc.value.status_code == 400
    assert "Already a member" in exc.value.detail


def test_asking_twice_returns_the_same_request():
    db = _DB([_group(), None, SimpleNamespace(id="jr_existing")])

    res = _call(db, group=_group())

    assert res["existing"] is True
    assert res["request_id"] == "jr_existing"
    assert db.added == []  # nothing stacked up for the owner


def test_a_new_request_is_recorded_and_committed():
    db = _DB([_group(), None, None])

    res = _call(db, group=_group(), body={"message": "B 机上的 coder，申请加入"})

    assert res["existing"] is False
    assert res["request_id"].startswith("jr_")
    assert db.commits == 1
    assert len(db.added) == 1
    row = db.added[0]
    assert row.group_id == "g-private"
    assert row.user_id == "7"
    assert row.status == "pending"
    assert "申请加入" in row.message


def test_an_unknown_group_is_404():
    db = _DB([None])

    with pytest.raises(HTTPException) as exc:
        _call(db, group=None)

    assert exc.value.status_code == 404
    assert db.added == []
