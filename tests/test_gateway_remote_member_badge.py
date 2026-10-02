"""Registering an account from a paired machine marks it remote in the member list.

The host already verifies the peer token at registration; this covers keeping the
peer and surfacing it as ``is_remote`` / ``remote_label`` on the member. A local
registration (node secret, or no token) must stay non-remote.
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

from opensquad import remote_members  # noqa: E402
from opensquad.gateway.backend.app import api as gw  # noqa: E402


class _Request:
    """Just what register() touches: headers and the raw JSON body."""

    def __init__(self, body: dict, headers: dict | None = None):
        self._body = json.dumps(body).encode("utf-8")
        self.headers = headers or {}

    async def body(self):
        return self._body


class _Status(str):
    """Stand-in for User.status: a str for the response, with a .value for _member_info."""

    @property
    def value(self) -> str:
        return str(self)


_OFFLINE = _Status("offline")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(syscfg, "workspace_data_dir", lambda *parts: str(tmp_path / "_".join(parts)))
    monkeypatch.setattr(syscfg, "node_secret", lambda: "sec")
    # Fresh caches so one test's origins do not leak into the next.
    monkeypatch.setattr(gw, "_REMOTE_ORIGIN_CACHE", {})
    monkeypatch.setattr(gw, "_REMOTE_ORIGIN_CACHE_TS", 0.0)

    created: list = []

    async def _get_user_by_email(db, email):
        return None

    async def _create_user(db, user_data):
        user = SimpleNamespace(id="900001", email=user_data.email, name=user_data.name, avatar="", status=_OFFLINE)
        created.append(user)
        return user

    async def _bootstrap(db, user, language=None):
        return None

    monkeypatch.setattr(gw, "get_user_by_email", _get_user_by_email)
    monkeypatch.setattr(gw, "create_user", _create_user)
    monkeypatch.setattr(gw, "_bootstrap_default_group", _bootstrap)
    monkeypatch.setattr(gw, "create_access_token", lambda data: "tok")
    monkeypatch.setattr(gw, "_sync_cli_credentials", lambda *a, **k: None)
    return SimpleNamespace(created=created)


def _register_handler():
    for route in gw.router.routes:
        path = getattr(route, "path", "")
        if path.endswith("/auth/register") and "POST" in (getattr(route, "methods", set()) or set()):
            return route.endpoint
    raise AssertionError("POST /auth/register route not found")


def _register(user_data, request):
    return asyncio.run(_register_handler()(user_data=user_data, request=request, db=SimpleNamespace()))


def _user_data(email="coder-002@ai", name="coder-002"):
    return SimpleNamespace(email=email, name=name, password="pw", avatar="", language=None)


def test_registering_via_a_peer_token_records_the_origin_machine(env, monkeypatch):
    import opensquad.node_peers as np

    monkeypatch.setattr(
        np,
        "verify",
        lambda token: {"id": "peer_abc", "name": "office-pc", "scopes": ("agent:register", "group:join")},
    )

    _register(_user_data(), _Request({}, headers={"X-Node-Token": "peer-tok"}))

    assert remote_members.is_remote("coder-002@ai") is True
    assert remote_members.label("coder-002@ai") == "office-pc"


def test_registering_locally_does_not_mark_the_account_remote(env, monkeypatch):
    import opensquad.node_peers as np

    monkeypatch.setattr(np, "verify", lambda token: None)

    _register(_user_data(), _Request({}, headers={"X-Node-Secret": "sec"}))

    assert remote_members.is_remote("coder-002@ai") is False


def test_a_token_without_the_register_scope_is_refused_and_not_recorded(env, monkeypatch):
    import opensquad.node_peers as np

    monkeypatch.setattr(np, "verify", lambda token: {"id": "peer_abc", "name": "office-pc", "scopes": ("board:read",)})

    with pytest.raises(HTTPException) as exc:
        _register(_user_data(), _Request({}, headers={"X-Node-Token": "weak"}))

    assert exc.value.status_code == 400  # falls through to the web gate
    assert remote_members.is_remote("coder-002@ai") is False


def test_member_info_carries_the_remote_flag_and_label(env, monkeypatch):
    import opensquad.node_peers as np

    monkeypatch.setattr(
        np, "verify", lambda token: {"id": "peer_abc", "name": "office-pc", "scopes": ("agent:register",)}
    )
    _register(_user_data(), _Request({}, headers={"X-Node-Token": "peer-tok"}))

    remote_user = SimpleNamespace(id="900001", email="coder-002@ai", name="coder-002", avatar="", status=_OFFLINE)
    local_user = SimpleNamespace(id="1", email="owner@example.com", name="owner", avatar="", status=_OFFLINE)

    remote_info = gw._member_info(remote_user)
    local_info = gw._member_info(local_user)

    assert remote_info.is_remote is True
    assert remote_info.remote_label == "office-pc"
    assert local_info.is_remote is False
    assert local_info.remote_label is None
