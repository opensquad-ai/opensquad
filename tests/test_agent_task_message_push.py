"""An agent's task-window message must be pushed, not only readable.

The user's message in a task window is fanned out by the endpoint that receives it. An agent's is
a plain board write — appended as a discussion item — and nothing followed it, so a worker on a
paired machine could read it over the board API and never saw it arrive. A live run showed exactly
that ("pull works, push does not") and confirmed it from the owner's outbox, where the only
task:relay event was a uuid4 — the collaboration invite — and none of the three test messages.

The fix is one hop: the writer announces the message to the machine that owns the board, and that
machine fans it out with the same call the user's messages use.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad import collab_board as cb  # noqa: E402


def _source(rel: str) -> str:
    return (_SRC / rel).read_text(encoding="utf-8")


def test_the_writer_announces_the_message_and_the_endpoint_fans_it_out():
    """The two halves of the hop, and their order in the tool: write first, then announce."""
    tool = _source("opensquad/tools/collaboration.py")
    route = _source("opensquad/gateway/backend/app/ai_web/routes/_main.py")

    assert "notify_task_message(collab_id, text, author=agent_id)" in tool
    assert tool.index("append_public_discussion(") < tool.index("notify_task_message(collab_id, text"), (
        "the board write comes first: the push is about a message that exists"
    )

    assert '@router.post("/collab-board/tasks/{task_id}/notify")' in route
    assert "await relay.fan_out_task(" in route
    assert '"channel": "task"' in route, "it must land on the task channel, like the user's message"
    assert '"event_id": uuid.uuid4().hex' in route, "each announce is its own event for the dedup"


def test_a_collaboration_without_a_group_says_so_instead_of_pushing():
    route = _source("opensquad/gateway/backend/app/ai_web/routes/_main.py")
    block = route[route.index('@router.post("/collab-board/tasks/{task_id}/notify")') :]

    assert "this collaboration has no group" in block[:2600]
    assert 'has_scope(peer, "board:write")' in block[:2600], "the same door as the board bridge"


def test_the_client_helper_targets_the_owner_and_reuses_the_auth(monkeypatch):
    """One browser of the helper: it goes to the board's owner, on loopback when that is us."""
    seen: list[tuple[str, dict]] = []

    class _Resp:
        def read(self) -> bytes:
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

    def _urlopen(req, timeout=0):
        seen.append((req.full_url, dict(req.headers)))
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    monkeypatch.setattr(cb, "board_base_url", lambda **kw: "")
    monkeypatch.setattr(cb, "_board_auth_headers", lambda base: {"X-Node-Secret": "s"})

    class _Cfg:
        @staticmethod
        def port(name: str) -> int:
            return 9555

    monkeypatch.setattr("opensquad.system_config.syscfg", _Cfg())

    ok = cb.notify_task_message("77549D", "hello", author="agent305")

    assert ok is True
    url, headers = seen[0]
    assert url == "http://127.0.0.1:9555/api/ai-web/collab-board/tasks/77549D/notify"
    # urllib normalises header names, so compare them case-insensitively
    lowered = {k.lower(): v for k, v in headers.items()}
    assert lowered.get("x-node-secret") == "s"


def test_a_failed_announce_is_swallowed_not_raised(monkeypatch):
    """The message is already on the board; a push problem must not fail the tool."""
    monkeypatch.setattr(cb, "board_base_url", lambda **kw: "http://192.168.5.4:9555")
    monkeypatch.setattr(cb, "_board_auth_headers", lambda base: {})

    def _boom(req, timeout=0):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _boom)

    assert cb.notify_task_message("77549D", "hello") is False


def test_the_headers_helper_is_shared_with_the_board_rpc():
    """The drift that cost a debugging session: two call sites, one token lookup."""
    src = _source("opensquad/collab_board.py")

    assert src.count("_board_auth_headers(base)") >= 2, "the notify and the RPC must share it"
    assert "peer_bridge.peer_token(base)" in src


def test_the_endpoint_needs_a_secret_or_a_scoped_peer():
    """Unauthenticated announces must be refused (it triggers a push to every subscriber)."""
    route = _source("opensquad/gateway/backend/app/ai_web/routes/_main.py")
    block = route[route.index('@router.post("/collab-board/tasks/{task_id}/notify")') :][:2600]

    assert '_check_node_secret(request.headers.get("X-Node-Secret", ""))' in block
    assert 'raise HTTPException(status_code=401, detail="Invalid or missing node secret (task notify)")' in block


def test_the_tool_wiring_survives_a_missing_helper(monkeypatch):
    """Sanity: the announcement is a separate try, so an import problem cannot break posting."""
    tool = _source("opensquad/tools/collaboration.py")
    block = tool[tool.index("notify_task_message(collab_id, text") - 400 :]

    assert block.count("try:") >= 1 and block.count("except Exception:") >= 1
