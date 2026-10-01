"""Remote collaboration boards: owned by the gateway, forwarded by agents."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from opensquad import collab_board as cb
from opensquad.system_config import syscfg


@pytest.fixture()
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cb, "_board_owners_file", lambda: str(tmp_path / "board_owners.json"))
    return cb


@pytest.fixture()
def gateway_route():
    backend = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway" / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from opensquad.gateway.backend.app.ai_web.routes import _main as routes

    return routes


class _BoardServer(BaseHTTPRequestHandler):
    """Stands in for the other machine's gateway."""

    received: list = []
    payload: dict = {"ok": True, "result": {"from": "gateway"}}

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) or b"{}"
        type(self).received.append(
            {
                "path": self.path,
                "secret": self.headers.get("X-Node-Secret"),
                "token": self.headers.get("X-Node-Token"),
                "body": json.loads(raw),
            }
        )
        data = json.dumps(type(self).payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # keep the test output clean
        pass


@pytest.fixture()
def board_server():
    _BoardServer.received = []
    _BoardServer.payload = {"ok": True, "result": {"from": "gateway"}}
    server = HTTPServer(("127.0.0.1", 0), _BoardServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _BoardServer
    finally:
        server.shutdown()


# --------------------------------------------------------------------------
# Local behaviour is unchanged
# --------------------------------------------------------------------------
def test_local_call_bypasses_forwarding(board, monkeypatch):
    # even with a remote configured, the owner of the board runs the real code
    monkeypatch.setenv("OPENSQUAD_BOARD_URL", "http://192.0.2.1:9555")
    rec = cb.local_call("create_task", task_name="跨机任务", created_by="pm")
    assert rec["task_id"]
    assert cb.local_call("get_task", task_id=rec["task_id"])["task_name"] == "跨机任务"


def test_unknown_op_is_rejected(board):
    with pytest.raises(KeyError):
        cb.local_call("nope")


def test_single_machine_installs_stay_local(board, monkeypatch):
    monkeypatch.delenv("OPENSQUAD_BOARD_URL", raising=False)
    monkeypatch.setattr(cb, "_agent_config", lambda: {"group_chat": {"base_url": "http://127.0.0.1:9555"}})
    assert cb.board_base_url() == ""
    rec = cb.create_task(task_name="本地任务", created_by="pm")
    assert cb.get_task(task_id=rec["task_id"])["task_name"] == "本地任务"


def test_mode_local_wins_over_a_remote_chat_bridge(board, monkeypatch):
    monkeypatch.delenv("OPENSQUAD_BOARD_URL", raising=False)
    monkeypatch.setattr(
        cb,
        "_agent_config",
        lambda: {"group_chat": {"base_url": "http://192.168.1.20:9555"}, "collab_board": {"mode": "local"}},
    )
    assert cb.board_base_url() == ""


def test_auto_mode_follows_the_chat_bridge(board, monkeypatch):
    monkeypatch.delenv("OPENSQUAD_BOARD_URL", raising=False)
    monkeypatch.setattr(cb, "_agent_config", lambda: {"group_chat": {"base_url": "ws://192.168.1.20:9555/ws"}})
    assert cb.board_base_url() == "http://192.168.1.20:9555"
    # an explicit url always wins
    monkeypatch.setattr(
        cb,
        "_agent_config",
        lambda: {
            "group_chat": {"base_url": "http://192.168.1.20:9555"},
            "collab_board": {"url": "https://board.example.com/"},
        },
    )
    assert cb.board_base_url() == "https://board.example.com"


# --------------------------------------------------------------------------
# Forwarding
# --------------------------------------------------------------------------
def test_agent_forwards_calls_to_the_owning_gateway(board, board_server, monkeypatch):
    base, server = board_server
    monkeypatch.setenv("OPENSQUAD_BOARD_URL", base)
    monkeypatch.setattr(syscfg, "node_secret", lambda: "sec")

    result = cb.list_tasks()

    assert result == {"from": "gateway"}  # the gateway's answer, not a local read
    assert len(server.received) == 1
    sent = server.received[0]
    assert sent["path"] == "/api/ai-web/agent/board"
    assert sent["secret"] == "sec"
    assert sent["body"]["op"] == "list_tasks"
    # nothing was written to this machine's board
    assert not Path(cb._board_dir(), "board_tasks.json").exists()


def test_forwarded_writes_carry_their_kwargs(board, board_server, monkeypatch):
    base, server = board_server
    monkeypatch.setenv("OPENSQUAD_BOARD_URL", base)
    monkeypatch.setattr(syscfg, "node_secret", lambda: "sec")

    cb.upsert_item(collab_id="AB12CD", agent_id="coder", item_type="task", title="实现")

    body = server.received[0]["body"]
    assert body["op"] == "upsert_item"
    assert body["kwargs"]["collab_id"] == "AB12CD"
    assert body["kwargs"]["item_type"] == "task"


def test_transport_failure_is_loud_and_never_falls_back(board, monkeypatch):
    monkeypatch.delenv("OPENSQUAD_BOARD_URL", raising=False)
    # an unroutable address: the client must not quietly write a local board
    monkeypatch.setattr(cb, "_agent_config", lambda: {"group_chat": {"base_url": "http://192.0.2.1:9"}})
    monkeypatch.setattr(cb, "_BOARD_TIMEOUT", 1.0)

    with pytest.raises(cb.BoardRemoteError):
        cb.create_task(task_name="跨机任务", created_by="pm")
    assert not Path(cb._board_dir(), "board_tasks.json").exists()


# --------------------------------------------------------------------------
# A board joined on a paired machine belongs to that machine
#
# Pairing no longer repoints the home bridge, so a group joined there is no
# longer "remote" by the old test. The owner has to be recorded when the agent
# joins, and read back when a board call about that group (or its collab task)
# comes in.
# --------------------------------------------------------------------------
def test_a_group_joined_on_a_peer_routes_its_board_there(board, board_server, monkeypatch):
    base, server = board_server
    monkeypatch.delenv("OPENSQUAD_BOARD_URL", raising=False)
    # home bridge is loopback: by the legacy rule this would stay local
    monkeypatch.setattr(cb, "_agent_config", lambda: {"group_chat": {"base_url": "http://127.0.0.1:9555"}})
    monkeypatch.setattr(syscfg, "node_secret", lambda: "sec")
    import opensquad.node_peers as node_peers

    monkeypatch.setattr(node_peers, "load_local_peer", lambda host: {"token": "peer-tok"})
    import opensquad.peer_bridge as peer_bridge

    monkeypatch.setattr(
        peer_bridge,
        "peer_for_group",
        lambda group: {"host": "192.168.5.4", "base_url": base, "token": "peer-tok"},
    )
    monkeypatch.setattr(peer_bridge, "find_peer", lambda host: {"host": "192.168.5.4", "base_url": base})

    result = cb.create_task(task_name="跨机任务", created_by="pm", group_id="g-7f3a")

    assert result == {"from": "gateway"}
    assert len(server.received) == 1
    assert server.received[0]["token"] == "peer-tok"  # the peer token, not node_secret
    assert server.received[0]["body"]["kwargs"]["group_id"] == "g-7f3a"
    assert not Path(cb._board_dir(), "board_tasks.json").exists()


def test_a_collab_task_remembers_the_machine_that_owns_its_group(board, board_server, monkeypatch):
    base, server = board_server
    monkeypatch.delenv("OPENSQUAD_BOARD_URL", raising=False)
    monkeypatch.setattr(cb, "_agent_config", lambda: {"group_chat": {"base_url": "http://127.0.0.1:9555"}})
    monkeypatch.setattr(syscfg, "node_secret", lambda: "sec")
    import opensquad.node_peers as node_peers

    monkeypatch.setattr(node_peers, "load_local_peer", lambda host: {"token": "peer-tok"})
    import opensquad.peer_bridge as peer_bridge

    monkeypatch.setattr(
        peer_bridge,
        "peer_for_group",
        lambda group: {"host": "192.168.5.4", "base_url": base, "token": "peer-tok"},
    )
    monkeypatch.setattr(peer_bridge, "find_peer", lambda host: {"host": "192.168.5.4", "base_url": base})

    assert cb.remember_board_owner("AB12CD", "192.168.5.4") is True
    # a later call that carries only the collab_id still routes to the owner
    cb.update_task(task_id="AB12CD", progress=50)

    assert len(server.received) == 1
    assert server.received[0]["body"]["op"] == "update_task"


def test_a_local_group_and_task_stay_local(board, monkeypatch):
    monkeypatch.delenv("OPENSQUAD_BOARD_URL", raising=False)
    monkeypatch.setattr(cb, "_agent_config", lambda: {"group_chat": {"base_url": "http://127.0.0.1:9555"}})
    import opensquad.peer_bridge as peer_bridge

    monkeypatch.setattr(peer_bridge, "peer_for_group", lambda group: None)

    assert cb.board_owner(group_id="g-home") == ""
    assert cb.board_owner(collab_id="LOCAL1") == ""
    rec = cb.create_task(task_name="本地任务", created_by="pm", group_id="g-home")
    assert cb.get_task(task_id=rec["task_id"])["task_name"] == "本地任务"
    assert cb.get_task(task_id=rec["task_id"])["extra"]["group_id"] == "g-home"


# --------------------------------------------------------------------------
# The gateway side
# --------------------------------------------------------------------------
def _request(secret: str | None):
    headers = {"X-Node-Secret": secret} if secret is not None else {}
    return SimpleNamespace(headers=headers)


def test_gateway_board_bridge_creates_on_its_own_board(gateway_route, board, monkeypatch):
    monkeypatch.setattr(syscfg, "node_secret", lambda: "sec")

    res = asyncio.run(
        gateway_route.agent_board_op(
            gateway_route.BoardOpRequest(
                op="create_task", kwargs={"task_name": "跨机任务", "created_by": "remote-agent"}
            ),
            _request("sec"),
        )
    )
    assert res["ok"] is True
    task_id = res["result"]["task_id"]
    # the id comes from this gateway, and the task exists on this machine's board
    assert cb.local_call("get_task", task_id=task_id)["created_by"] == "remote-agent"

    listed = asyncio.run(gateway_route.agent_board_op(gateway_route.BoardOpRequest(op="list_tasks"), _request("sec")))
    assert [t["task_id"] for t in listed["result"]] == [task_id]


def test_gateway_board_bridge_rejects_bad_or_missing_secret(gateway_route, board, monkeypatch):
    monkeypatch.setattr(syscfg, "node_secret", lambda: "sec")
    for secret in (None, "", "wrong"):
        with pytest.raises(Exception) as exc:
            asyncio.run(
                gateway_route.agent_board_op(
                    gateway_route.BoardOpRequest(op="list_tasks"),
                    _request(secret),
                )
            )
        assert exc.value.status_code == 401


def test_gateway_board_bridge_fails_closed_without_a_secret(gateway_route, board, monkeypatch):
    monkeypatch.setattr(syscfg, "node_secret", lambda: "")
    with pytest.raises(Exception) as exc:
        asyncio.run(gateway_route.agent_board_op(gateway_route.BoardOpRequest(op="list_tasks"), _request("anything")))
    assert exc.value.status_code == 401


def test_gateway_board_bridge_rejects_unknown_ops(gateway_route, board, monkeypatch):
    monkeypatch.setattr(syscfg, "node_secret", lambda: "sec")
    with pytest.raises(Exception) as exc:
        asyncio.run(gateway_route.agent_board_op(gateway_route.BoardOpRequest(op="_read_tasks"), _request("sec")))
    assert exc.value.status_code == 400
