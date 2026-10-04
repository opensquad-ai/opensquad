"""A worker that joins must stop looking invited on the collaboration card.

Reported from a live run: the task window listed all three participants as accepted, while the
card in the group chat still showed one of them as invited. Both read the truth they have — the
card is a message holding a snapshot of who was invited when it was sent, and the board is where
join_collaboration records the accept. The board is written by the agent process; the card is a
message the gateway owns, so nothing carried the state across.

The user's own accept had always kept them in step, because the respond endpoint runs in the
gateway and rewrites the card there. These tests pin the same rewrite for the agent path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad import collab_board as cb  # noqa: E402


def _source(rel: str) -> str:
    return (_SRC / rel).read_text(encoding="utf-8")


def test_the_join_announces_the_accept_after_recording_it():
    tool = _source("opensquad/tools/collaboration.py")

    assert 'announce_participant(collab_id, _agent_id, "accepted")' in tool
    assert tool.index('mark_participant(collab_id=collab_id, agent_id=_agent_id, state="accepted")') < tool.index(
        'announce_participant(collab_id, _agent_id, "accepted")'
    ), "the board is written first: the card rewrite reports state that exists"
    assert "except Exception:" in tool


def test_the_endpoint_rewrites_the_card_and_tells_the_clients():
    route = _source("opensquad/gateway/backend/app/api.py")
    block = route[route.index('@router.post("/collab-board/tasks/{collab_id}/participant")') :][:3200]

    assert 'patch_collab_task_participant_in_content(message.content or "", participant, state)' in block
    assert "message.is_edited = True" in block
    assert "await notify_message_update(" in block, "every surface must be told, not just the DB"
    assert "this collaboration has no group" in block
    assert "no collaboration card for this task in its group" in block


def test_the_endpoint_uses_the_board_bridge_door():
    route = _source("opensquad/gateway/backend/app/api.py")
    block = route[route.index('@router.post("/collab-board/tasks/{collab_id}/participant")') :][:1800]

    assert '_check_node_secret(request.headers.get("X-Node-Secret", ""))' in block
    assert 'has_scope(peer, "board:write")' in block
    assert 'detail="Invalid or missing node secret (participant)"' in block


def test_the_client_helper_targets_the_owner_with_a_board_authenticated_post(monkeypatch):
    seen: list[tuple[str, dict, bytes]] = []

    class _Resp:
        def read(self) -> bytes:
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

    def _urlopen(req, timeout=0):
        seen.append((req.full_url, {k.lower(): v for k, v in req.headers.items()}, req.data))
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    monkeypatch.setattr(cb, "board_base_url", lambda **kw: "")
    monkeypatch.setattr(cb, "_board_auth_headers", lambda base: {"X-Node-Secret": "s"})

    class _Cfg:
        @staticmethod
        def port(name: str) -> int:
            return 9555

    monkeypatch.setattr("opensquad.system_config.syscfg", _Cfg())

    ok = cb.announce_participant("B15890", "agent305", "accepted")

    assert ok is True
    url, headers, body = seen[0]
    assert url == "http://127.0.0.1:9555/api/ai-web/collab-board/tasks/B15890/participant"
    assert headers.get("x-node-secret") == "s"
    assert json.loads(body.decode("utf-8")) == {"agent_id": "agent305", "state": "accepted"}


def test_a_failed_rewrite_does_not_raise_and_does_not_fail_the_join(monkeypatch):
    monkeypatch.setattr(cb, "board_base_url", lambda **kw: "http://192.168.5.4:9555")
    monkeypatch.setattr(cb, "_board_auth_headers", lambda base: {})

    def _boom(req, timeout=0):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _boom)

    assert cb.announce_participant("B15890", "agent305", "accepted") is False
