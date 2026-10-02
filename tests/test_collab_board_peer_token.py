"""A paired machine drives the owner's board with its peer token, from either store.

Pairing records the token in the agent's config; the workspace peer store is not always
where it ends up (re-pairing, a migrated install). Reading only one of them sent an
empty header and the owner answered 401 "Invalid or missing node secret" — the PM's
report then landed on its own board instead of the owner's.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import opensquad.collab_board as cb  # noqa: E402
import opensquad.node_peers as node_peers  # noqa: E402
import opensquad.peer_bridge as peer_bridge  # noqa: E402


class _Response:
    status = 200

    def read(self):
        return b'{"ok": true, "result": null}'

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _call(monkeypatch, store_token: str, config_token: str):
    seen: dict = {}
    monkeypatch.setattr(cb, "board_base_url", lambda **kwargs: "http://192.168.5.4:9555")
    monkeypatch.setattr(node_peers, "load_local_peer", lambda base: {"token": store_token} if store_token else {})
    monkeypatch.setattr(peer_bridge, "peer_token", lambda host: config_token)

    def _urlopen(request, timeout=None):
        seen["headers"] = {k.lower(): v for k, v in dict(request.header_items()).items()}
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    cb._remote_call("get_task", (), {"task_id": "T1"})
    return seen["headers"]


def test_the_token_is_read_from_the_workspace_store_when_it_is_there(monkeypatch):
    headers = _call(monkeypatch, store_token="store-tok", config_token="config-tok")

    assert headers["x-node-token"] == "store-tok"


def test_and_from_the_agent_config_when_the_store_is_empty(monkeypatch):
    """The field failure: pairing had recorded it in config, the store was empty, and
    the request went out without a token."""
    headers = _call(monkeypatch, store_token="", config_token="config-tok")

    assert headers["x-node-token"] == "config-tok"
