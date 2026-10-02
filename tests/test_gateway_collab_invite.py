"""The invite endpoint: an invitation has to reach the agent, not just the group.

A group post only wakes a strict agent when the @name matches its IM name; an invitation
whose name does not is never seen, and the invitee sits at 已邀请 forever. This endpoint
hands it to each named agent's control channel as a directed, wake-worthy frame.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
_BACKEND_DIR = _SRC / "opensquad" / "gateway" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import app.ai_web.registry as agent_registry  # noqa: E402
import app.ai_web.routes._main as routes  # noqa: E402
import app.ai_web.websocket as gw_ws  # noqa: E402
import app.relay as gw_relay  # noqa: E402


def _request(headers: dict | None = None):
    return SimpleNamespace(headers=headers or {})


@pytest.fixture()
def env(monkeypatch):
    sent: list[tuple] = []

    async def _send(agent_id, message):
        sent.append((agent_id, message))
        return True

    monkeypatch.setattr(agent_registry, "send_to_agent", _send, raising=False)
    monkeypatch.setattr(gw_ws, "_check_node_secret", lambda secret: secret == "good")

    relayed: list[tuple] = []

    async def _fan(group_id, chat, origin_host=""):
        relayed.append((group_id, chat))
        return {"ok": True}

    monkeypatch.setattr(gw_relay, "fan_out_task", _fan, raising=False)
    return {"sent": sent, "relayed": relayed}


def _body(**over):
    payload = {
        "collab_id": "T1",
        "card": "software_dev_team",
        "group_id": "g-1",
        "message": "确认参与",
        "agents": ["pm", "agent305"],
    }
    payload.update(over)
    return routes.CollabInviteRequest(**payload)


def test_the_invite_reaches_each_agent_as_a_directed_wake_frame(env):
    res = asyncio.run(routes.collab_invite(_body(), _request({"X-Node-Secret": "good"})))

    assert res["notified"] == ["pm", "agent305"]
    agent, payload = env["sent"][1]
    assert agent == "agent305"
    assert payload["wake"] is True
    assert payload["mentions"] == ["agent305"]
    assert payload["channel"] == "task"
    assert payload["collab_id"] == "T1"
    # and it is relayed to the group's subscribers on paired machines
    assert env["relayed"][0][0] == "g-1"
    assert env["relayed"][0][1]["event_id"]


def test_it_refuses_without_a_secret_or_a_scoped_peer_token(env, monkeypatch):
    import opensquad.node_peers as node_peers

    monkeypatch.setattr(node_peers, "verify", lambda token: None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.collab_invite(_body(), _request({})))

    assert exc.value.status_code == 401
    assert env["sent"] == []


def test_a_peer_token_without_group_scope_is_refused(env, monkeypatch):
    import opensquad.node_peers as node_peers

    monkeypatch.setattr(node_peers, "verify", lambda token: {"id": "peer_1", "scopes": ("board:read",)})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.collab_invite(_body(), _request({"X-Node-Token": "weak"})))

    assert exc.value.status_code == 401


def test_a_peer_token_with_group_scope_is_accepted(env, monkeypatch):
    import opensquad.node_peers as node_peers

    monkeypatch.setattr(node_peers, "verify", lambda token: {"id": "peer_1", "scopes": ("group:join",)})

    res = asyncio.run(routes.collab_invite(_body(), _request({"X-Node-Token": "ok"})))

    assert res["ok"] is True


def test_an_empty_invitation_is_refused(env):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.collab_invite(_body(agents=[]), _request({"X-Node-Secret": "good"})))

    assert exc.value.status_code == 400
