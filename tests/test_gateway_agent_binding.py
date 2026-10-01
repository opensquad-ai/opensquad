"""Registering an @ai email from a second node is refused, not handed over.

The gateway binds an @ai account to the node that first claimed it. This is the
route-level half of that rule; the store itself is covered by test_agent_identity.
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

from opensquad import agent_identity  # noqa: E402
from opensquad.gateway.backend.app import api as gw  # noqa: E402


class _Request:
    """Just what register_user touches: headers and the raw JSON body."""

    def __init__(self, body: dict, secret: str = "sec"):
        self._body = json.dumps(body).encode("utf-8")
        self.headers = {"X-Node-Secret": secret}

    async def body(self):
        return self._body


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(syscfg, "workspace_data_dir", lambda *parts: str(tmp_path / "_".join(parts)))
    monkeypatch.setattr(syscfg, "node_secret", lambda: "sec")

    existing = SimpleNamespace(id="2", email="coder-001@ai", name="coder-001")

    async def _get_user_by_email(db, email):
        return existing if str(email).lower() == existing.email else None

    async def _create_user(db, user_data):
        raise AssertionError("these paths must never create a second account")

    monkeypatch.setattr(gw, "get_user_by_email", _get_user_by_email)
    monkeypatch.setattr(gw, "create_user", _create_user)
    return SimpleNamespace(email="coder-001@ai", password="pw", name="coder-001")


def _register_handler():
    """Find the POST /auth/register endpoint without hardcoding its name."""
    for route in gw.router.routes:
        path = getattr(route, "path", "")
        if path.endswith("/auth/register") and "POST" in (getattr(route, "methods", set()) or set()):
            return route.endpoint
    raise AssertionError("POST /auth/register route not found")


def _register(user_data, body: dict, secret: str = "sec"):
    return asyncio.run(_register_handler()(user_data=user_data, request=_Request(body, secret), db=SimpleNamespace()))


def test_a_second_node_is_refused_with_the_owner_named(env):
    agent_identity.bind(env.email, "machine-a-coder")

    with pytest.raises(HTTPException) as exc:
        _register(env, {"agent_uid": "machine-b-coder"})

    assert exc.value.status_code == 409
    assert "machine-a-coder" in exc.value.detail
    assert "unique @ai email" in exc.value.detail


def test_the_owning_node_is_not_blocked(env):
    """Same node again: the guard lets it through to the normal 'exists' answer."""
    agent_identity.bind(env.email, "machine-a-coder")

    with pytest.raises(HTTPException) as exc:
        _register(env, {"agent_uid": "machine-a-coder"})

    assert exc.value.status_code == 400  # "Email already registered", not 409


def test_a_client_without_a_uid_keeps_the_old_behaviour(env):
    agent_identity.bind(env.email, "machine-a-coder")

    with pytest.raises(HTTPException) as exc:
        _register(env, {})

    assert exc.value.status_code == 400


def test_an_existing_web_gate_still_runs_first(env):
    """Without the node secret, an @ai email is rejected before any binding logic."""
    with pytest.raises(HTTPException) as exc:
        _register(env, {"agent_uid": "machine-b-coder"}, secret="wrong")

    assert exc.value.status_code == 400
    assert "@ai is reserved for agents" in exc.value.detail
