"""Tests for Bridge IM login/register and add-agent user resolution."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_BACKEND_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.pardir,
    "src",
    "opensquad",
    "gateway",
    "backend",
)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


class _Resp:
    def __init__(self, status_code: int, payload: dict | str | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or (payload if isinstance(payload, str) else "")

    def json(self):
        if isinstance(self._payload, dict):
            return self._payload
        raise ValueError("not json")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_bridge_login_registers_with_node_secret(monkeypatch):
    from opensquad.bridge import ChatProBridge

    calls: list[tuple] = []

    def fake_post(_self, url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers or {}))
        if url.endswith("/api/auth/login") and len([c for c in calls if c[0].endswith("/login")]) == 1:
            return _Resp(401, {"detail": "Incorrect email or password"})
        if url.endswith("/api/auth/register"):
            assert headers and headers.get("X-Node-Secret") == "test-node-secret"
            return _Resp(200, {"access_token": "t", "user": {"id": "1", "name": "Bot", "email": "bot@ai"}})
        if url.endswith("/api/auth/login"):
            return _Resp(200, {"access_token": "tok", "user": {"id": "99", "name": "Bot", "avatar": ""}})
        return _Resp(500, {"detail": "unexpected"})

    monkeypatch.setattr("opensquad.bridge.requests.Session.post", fake_post)
    monkeypatch.setattr("opensquad.bridge.syscfg.node_secret", lambda: "test-node-secret")

    b = ChatProBridge(base_url="http://gw", email="bot@ai", password="secret", agent_name="Bot")
    assert b.login() is True
    assert b.token == "tok"
    assert b.user_id == "99"
    assert any(u.endswith("/api/auth/register") for u, _, _ in calls)


def test_bridge_login_does_not_reset_when_register_400_not_exists(monkeypatch):
    from opensquad.bridge import ChatProBridge

    posts = []

    def fake_post(_self, url, json=None, headers=None, timeout=None):
        posts.append(url)
        if url.endswith("/login"):
            return _Resp(401, {"detail": "Incorrect"})
        if url.endswith("/register"):
            return _Resp(
                400,
                {"detail": "Please use a personal email for the web account (@ai is reserved for agents)."},
            )
        if url.endswith("/reset-password"):
            pytest.fail("password reset must not run for non-exists 400")
        return _Resp(500)

    monkeypatch.setattr("opensquad.bridge.requests.Session.post", fake_post)
    monkeypatch.setattr("opensquad.bridge.syscfg.node_secret", lambda: "secret")

    b = ChatProBridge(base_url="http://gw", email="ai@ai", password="aaaaaa", agent_name="ai")
    assert b.login() is False
    assert not any(u.endswith("/reset-password") for u in posts)


@pytest.mark.asyncio
async def test_resolve_agent_chat_user_by_email():
    import app.api as api_mod

    db = MagicMock()
    agent = {
        "agent_id": "news2theme_agent-001",
        "agent_name": "News2Theme",
        "dir_name": "news2theme_agent",
        "config": {"group_chat": {"email": "news2theme_agent@ai", "password": "pw", "enabled": True}},
    }
    existing = SimpleNamespace(id="555001", email="news2theme_agent@ai", name="News2Theme")

    with (
        patch.object(api_mod, "get_user_by_id", AsyncMock(return_value=None)),
        patch.object(api_mod, "get_user_by_email", AsyncMock(return_value=existing)),
        patch.object(api_mod, "create_user", AsyncMock()) as create_user,
    ):
        user = await api_mod._resolve_or_create_agent_chat_user(db, agent_id="news2theme_agent-001", agent=agent)
        assert user.id == "555001"
        create_user.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_agent_chat_user_creates_when_missing():
    import app.api as api_mod

    db = MagicMock()
    agent = {
        "agent_id": "news2theme_agent-001",
        "agent_name": "News2Theme",
        "config": {"group_chat": {"email": "news2theme_agent@ai", "password": "pw123456"}},
    }
    created = SimpleNamespace(id="777", email="news2theme_agent@ai", name="News2Theme")

    with (
        patch.object(api_mod, "get_user_by_id", AsyncMock(return_value=None)),
        patch.object(api_mod, "get_user_by_email", AsyncMock(return_value=None)),
        patch.object(api_mod, "create_user", AsyncMock(return_value=created)) as create_user,
    ):
        user = await api_mod._resolve_or_create_agent_chat_user(db, agent_id="news2theme_agent-001", agent=agent)
        assert user.id == "777"
        create_user.assert_awaited_once()


def test_im_register_account_rejects_placeholder(tmp_path, monkeypatch):
    from opensquad.tools import im as im_mod

    monkeypatch.setattr(im_mod.input_hub, "agent_dir", str(tmp_path))
    (tmp_path / "config.json").write_text('{"agent_name":"Bot"}', encoding="utf-8")
    out = im_mod.register_account("ai@ai", "aaaaaa", name="Bot")
    assert out["status"] == "error"
    assert "placeholder" in out["message"].lower() or "ai@ai" in out["message"]
