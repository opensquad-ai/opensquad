"""A paired machine's token opens exactly two doors: agent registration and the board.

Not node_secret — that is the point of pairing. These tests pin which scope opens
which door, and that a token without the scope (or a revoked one) opens nothing.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from opensquad.system_config import syscfg

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from opensquad import node_peers  # noqa: E402
from opensquad.gateway.backend.app import api as gw  # noqa: E402
from opensquad.gateway.backend.app.ai_web.routes import _main as routes  # noqa: E402


def _peer(scopes):
    return {"id": "peer_1", "name": "machine-b", "scopes": tuple(scopes)} if scopes is not None else None


@pytest.fixture()
def env(monkeypatch):
    """node_secret off, so anything that passes must be the peer token."""
    monkeypatch.setattr(syscfg, "node_secret", lambda: "")
    return SimpleNamespace(email="coder-001@ai", password="pw", name="coder-001")


class _RegisterRequest:
    def __init__(self, token: str = ""):
        self._body = json.dumps({"agent_uid": "machine-b-coder"}).encode("utf-8")
        self.headers = {"X-Node-Token": token} if token else {}

    async def body(self):
        return self._body


def _register_handler():
    for route in gw.router.routes:
        if getattr(route, "path", "").endswith("/auth/register") and "POST" in (
            getattr(route, "methods", set()) or set()
        ):
            return route.endpoint
    raise AssertionError("POST /auth/register not found")


def _register(env, monkeypatch, scopes):
    monkeypatch.setattr(node_peers, "verify", lambda tok: _peer(scopes) if tok == "peer-token" else None)

    async def _existing(db, email):
        return SimpleNamespace(id="2", email=email)

    monkeypatch.setattr(gw, "get_user_by_email", _existing)
    return asyncio.run(
        _register_handler()(
            user_data=env,
            request=_RegisterRequest("peer-token"),
            db=SimpleNamespace(),
        )
    )


# ── registration ───────────────────────────────────────────────────────────


def test_a_paired_peer_may_register_its_agents(env, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _register(env, monkeypatch, ("agent:register",))

    # it got past the "web cannot use @ai" gate, and stopped at "already exists"
    assert exc.value.status_code == 400
    assert "already registered" in exc.value.detail


def test_a_peer_without_the_scope_cannot_register(env, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _register(env, monkeypatch, ("board:read",))

    assert exc.value.status_code == 400
    assert "reserved for agents" in exc.value.detail


def test_a_revoked_or_unknown_token_cannot_register(env, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _register(env, monkeypatch, None)  # verify() returns None

    assert exc.value.status_code == 400
    assert "reserved for agents" in exc.value.detail


# ── the board bridge ───────────────────────────────────────────────────────


class _BoardRequest:
    def __init__(self, token: str = ""):
        self.headers = {"X-Node-Token": token} if token else {}


def _board(monkeypatch, scopes, token="peer-token"):
    """``scopes=None`` means "this token is unknown" (verify returns None)."""

    def _verify(tok):
        if scopes is None:
            return None
        return _peer(scopes) if tok == token else None

    monkeypatch.setattr(node_peers, "verify", _verify)

    def _local(op, *args, **kwargs):
        return {"op": op}

    import opensquad.collab_board as board_mod

    monkeypatch.setattr(board_mod, "local_call", _local, raising=False)
    return asyncio.run(routes.agent_board_op(routes.BoardOpRequest(op="list_tasks"), _BoardRequest(token)))


def test_the_board_accepts_a_peer_with_a_board_scope(monkeypatch):
    monkeypatch.setattr(syscfg, "node_secret", lambda: "")  # no node_secret in play

    res = _board(monkeypatch, ("board:read",))

    assert res["ok"] is True


def test_the_board_refuses_a_peer_without_a_board_scope(monkeypatch):
    monkeypatch.setattr(syscfg, "node_secret", lambda: "")

    with pytest.raises(HTTPException) as exc:
        _board(monkeypatch, ("agent:register",))

    assert exc.value.status_code == 401


def test_the_board_refuses_an_unknown_token(monkeypatch):
    monkeypatch.setattr(syscfg, "node_secret", lambda: "")

    with pytest.raises(HTTPException) as exc:
        _board(monkeypatch, None)  # header present, token unknown

    assert exc.value.status_code == 401
