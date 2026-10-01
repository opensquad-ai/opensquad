"""POST /auth/reset-password — a token may only reset its own password.

Found while wiring cross-machine agents: the route validated the bearer token but
never checked it belonged to the account named in the body, so any logged-in
account (an agent of a connected machine, or any @ai account) could reset anyone
else's password — the human account included.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from opensquad.system_config import syscfg

# The gateway backend uses absolute imports rooted at gateway/backend.
_BACKEND_DIR = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from opensquad.gateway.backend.app import api as gw  # noqa: E402


class _DB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def _user(uid: str, email: str):
    return SimpleNamespace(id=uid, email=email, hashed_password="old-hash")


async def _async(value):
    return value


@pytest.fixture()
def env(monkeypatch):
    """Two accounts; the node_secret path is off unless a test turns it on."""
    users = {"a@ai": _user("1", "a@ai"), "victim@x.com": _user("2", "victim@x.com")}
    monkeypatch.setattr(gw, "get_user_by_email", lambda db, email: _async(users.get(email)))
    monkeypatch.setattr(gw, "get_password_hash", lambda pw: f"hash:{pw}")
    monkeypatch.setattr(syscfg, "node_secret", lambda: "")
    return {"users": users}


def _run(email: str, *, token: str | None = None, node_secret: str = ""):
    db = _DB()
    authorization = f"Bearer {token}" if token is not None else None
    result = asyncio.run(
        gw.reset_password(
            reset_data={"email": email, "new_password": "new-pass", "node_secret": node_secret},
            db=db,
            authorization=authorization,
        )
    )
    return result, db


def _jwt(monkeypatch, subject: str):
    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod, "decode_token", lambda t: {"sub": subject} if t == "good" else None)


def test_a_token_can_only_reset_its_own_password(env, monkeypatch):
    _jwt(monkeypatch, "1")  # account 1's token ...
    victim = env["users"]["victim@x.com"]

    with pytest.raises(HTTPException) as exc:
        _run("victim@x.com", token="good")  # ... aimed at account 2

    assert exc.value.status_code == 403
    assert victim.hashed_password == "old-hash"  # untouched


def test_the_same_token_may_reset_itself(env, monkeypatch):
    _jwt(monkeypatch, "1")

    res, db = _run("a@ai", token="good")

    assert res == {"status": "ok"}
    assert db.commits == 1
    assert env["users"]["a@ai"].hashed_password == "hash:new-pass"


def test_node_secret_still_resets_any_agent_account(env, monkeypatch):
    """The agent auto-login path must keep working — it is why the route exists."""
    monkeypatch.setattr(syscfg, "node_secret", lambda: "real-secret")

    res, _ = _run("victim@x.com", node_secret="real-secret")

    assert res == {"status": "ok"}


def test_node_secret_with_the_wrong_value_is_rejected(env, monkeypatch):
    monkeypatch.setattr(syscfg, "node_secret", lambda: "real-secret")

    with pytest.raises(HTTPException) as exc:
        _run("victim@x.com", node_secret="wrong")

    assert exc.value.status_code == 401


def test_an_unset_node_secret_never_authorizes(env, monkeypatch):
    """SEC-11a must stay fixed while adding the ownership check."""
    monkeypatch.setattr(syscfg, "node_secret", lambda: "")

    with pytest.raises(HTTPException) as exc:
        _run("victim@x.com", node_secret="")

    assert exc.value.status_code == 401


def test_unknown_email_is_404(env, monkeypatch):
    monkeypatch.setattr(syscfg, "node_secret", lambda: "real-secret")

    with pytest.raises(HTTPException) as exc:
        _run("nobody@x.com", node_secret="real-secret")

    assert exc.value.status_code == 404
