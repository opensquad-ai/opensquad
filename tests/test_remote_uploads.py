"""Remote agents: which gateway to talk to, and where uploads actually live."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import opensquad.bridge as bridge_mod
from opensquad import collab_board as cb  # noqa: F401  (import order sanity)
from opensquad.input_hub import input_hub
from opensquad.system_config import syscfg
from opensquad.tools import im as im_tool

PNG_BYTES = b"\x89PNG\r\n\x1a\n-fake-image"


class _UploadServer(BaseHTTPRequestHandler):
    files: dict = {}
    hits: list = []

    def do_GET(self):  # noqa: N802
        type(self).hits.append(self.path)
        body = type(self).files.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output clean
        pass


@pytest.fixture()
def upload_server():
    _UploadServer.files = {"/uploads/pic.png": PNG_BYTES}
    _UploadServer.hits = []
    server = HTTPServer(("127.0.0.1", 0), _UploadServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _UploadServer
    finally:
        server.shutdown()


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """A workspace whose uploads dir is empty — i.e. another machine's agent."""
    monkeypatch.setattr(syscfg, "project_root", lambda: str(tmp_path))
    agent_dir = tmp_path / "agents" / "coder"
    agent_dir.mkdir(parents=True)
    yield {
        "root": tmp_path,
        "uploads": tmp_path / "data" / "uploads",
        "agent_dir": str(agent_dir),
    }
    input_hub.set_agent_context(None)


def _point_bridge_at(monkeypatch, base: str, *, treat_as_remote: bool) -> None:
    monkeypatch.setattr(bridge_mod.bridge, "base_url", base)
    if treat_as_remote:
        # the stub gateway runs on localhost, so neutralise the loopback guard
        monkeypatch.setattr(bridge_mod, "is_loopback_url", lambda url: False)


# --------------------------------------------------------------------------
# Gateway addressing
# --------------------------------------------------------------------------
def test_gateway_base_follows_the_bridge(monkeypatch):
    monkeypatch.setattr(bridge_mod.bridge, "base_url", "http://192.168.1.20:9555/")
    assert bridge_mod.gateway_base_url() == "http://192.168.1.20:9555"

    # without a bridge target it falls back to the local configuration
    monkeypatch.setattr(bridge_mod.bridge, "base_url", "")
    monkeypatch.setattr(syscfg, "gateway_http", lambda: "http://127.0.0.1:9555")
    assert bridge_mod.gateway_base_url() == "http://127.0.0.1:9555"


def test_uploads_prefix_is_the_gateway_url_when_remote(monkeypatch, tmp_path):
    monkeypatch.setattr(syscfg, "workspace_uploads_dir", lambda: str(tmp_path / "data" / "uploads"))
    monkeypatch.setattr(bridge_mod.bridge, "base_url", "http://192.168.1.20:9555")
    assert bridge_mod.uploads_display_prefix() == "http://192.168.1.20:9555/uploads"

    monkeypatch.setattr(bridge_mod.bridge, "base_url", "http://127.0.0.1:9555")
    assert bridge_mod.uploads_display_prefix() == str(tmp_path / "data" / "uploads").replace("\\", "/")


def test_get_history_keeps_upload_references_fetchable(monkeypatch):
    class _FakeBridge:
        token = "t"

        def get_group_history(self, group_id, limit=20):
            return [
                {
                    "sender_id": "u1",
                    "content": "看这张图 /uploads/pic.png",
                    "timestamp": 1,
                    "attachments": [{"name": "pic.png", "type": "image", "url": "/uploads/pic.png"}],
                }
            ]

    monkeypatch.setattr(bridge_mod, "bridge", _FakeBridge())
    # loopback target: attachments resolve locally here, so nothing dials out
    monkeypatch.setattr(bridge_mod, "gateway_base_url", lambda: "http://127.0.0.1:9555")
    monkeypatch.setattr(bridge_mod, "uploads_display_prefix", lambda: "http://192.168.1.20:9555/uploads")

    res = im_tool.get_history("g-default", limit=5)
    assert res["status"] == "success"
    content = res["history"][0]["content"]
    assert "http://192.168.1.20:9555/uploads/pic.png" in content


# --------------------------------------------------------------------------
# _fix_path
# --------------------------------------------------------------------------
def test_fix_path_fetches_the_upload_from_the_gateway(monkeypatch, upload_server, workspace):
    base, server = upload_server
    _point_bridge_at(monkeypatch, base, treat_as_remote=True)
    input_hub.set_agent_context(workspace["agent_dir"])

    resolved = input_hub._fix_path("/uploads/pic.png")

    # downloaded from the gateway and cached locally ...
    assert server.hits == ["/uploads/pic.png"]
    assert (workspace["uploads"] / "pic.png").read_bytes() == PNG_BYTES
    # ... then handed to the agent as its own private copy
    assert resolved.replace("\\", "/").endswith("agents/coder/data/uploads/pic.png")
    assert open(resolved, "rb").read() == PNG_BYTES


def test_fix_path_keeps_local_behaviour_without_a_remote_gateway(monkeypatch, workspace):
    monkeypatch.setattr(bridge_mod.bridge, "base_url", "http://127.0.0.1:9555")
    input_hub.set_agent_context(workspace["agent_dir"])

    resolved = input_hub._fix_path("/uploads/absent.png")

    # nothing was fetched and the previous local path is still what callers get
    assert not (workspace["uploads"] / "absent.png").exists()
    assert resolved.replace("\\", "/").endswith("data/uploads/absent.png")


def test_fix_path_survives_a_missing_remote_upload(monkeypatch, upload_server, workspace):
    base, server = upload_server
    _point_bridge_at(monkeypatch, base, treat_as_remote=True)
    input_hub.set_agent_context(workspace["agent_dir"])

    resolved = input_hub._fix_path("/uploads/nope.png")

    assert server.hits == ["/uploads/nope.png"]  # it tried
    assert not (workspace["uploads"] / "nope.png").exists()
    assert resolved.replace("\\", "/").endswith("data/uploads/nope.png")
