"""The 401 that took a hand-run sha256 to explain.

A paired machine's board write was refused with "Invalid or missing node secret" while it held a
token the owner had revoked. Neither side could say which of the four auth failures it was —
missing token, unknown token, revoked token, token without scope — and every peer record showed
``last_seen_at = null``, so "never used" and "used, then revoked" were indistinguishable. These
tests pin the three facts that turn it into a one-line answer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad import node_peers  # noqa: E402


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(node_peers, "store_file", lambda: str(tmp_path / "peers.json"))
    return tmp_path


def _mint(name: str = "ss-win-pc") -> dict:
    code = node_peers.start_pairing()["code"]
    req = node_peers.request_pairing(code, name=name)
    return node_peers.decide_pairing(req["request_id"], approve=True)


def _record(peer_id: str) -> dict:
    """The stored peer record (the shape the pair panel and this diagnosis both read)."""
    data = json.loads(Path(node_peers.store_file()).read_text(encoding="utf-8"))
    return data["peers"][peer_id]


def test_verifying_stamps_last_seen_so_never_used_is_visible(store):
    minted = _mint()

    assert not _record(minted["peer_id"]).get("last_seen_at")

    assert node_peers.verify(minted["token"]) is not None

    stamp = _record(minted["peer_id"]).get("last_seen_at")
    assert stamp, "a peer that authenticated must say so - that is what separates it from a dead one"
    # …and a rejected attempt must not stamp it, or the signal means nothing
    assert node_peers.verify("not-a-token") is None
    assert _record(minted["peer_id"]).get("last_seen_at") == stamp


def test_explain_names_a_revoked_token(store):
    minted = _mint()

    assert node_peers.explain(minted["token"]) == ""

    assert node_peers.revoke(minted["peer_id"]) is True

    why = node_peers.explain(minted["token"])

    assert "revoked" in why and "pair again" in why


def test_explain_names_the_other_failures(store):
    assert "no peer token" in node_peers.explain("")
    assert "not paired here" in node_peers.explain("never-issued")

    minted = _mint()
    # A token minted before board scopes existed (verify reads the stored tuple verbatim, it never
    # upgrades it) must be named rather than merely refused.
    path = Path(node_peers.store_file())
    data = json.loads(path.read_text(encoding="utf-8"))
    data["peers"][minted["peer_id"]]["scopes"] = ["agent:register"]
    path.write_text(json.dumps(data), encoding="utf-8")

    why = node_peers.explain(minted["token"])

    assert "no board scope" in why and "agent:register" in why


def test_the_gateway_and_the_client_both_name_the_reason():
    """Source locks: the board endpoint explains its 401, and the client says how to recover."""
    gw = (_SRC / "opensquad" / "gateway" / "backend" / "app" / "ai_web" / "routes" / "_main.py").read_text(
        encoding="utf-8"
    )
    client = (_SRC / "opensquad" / "collab_board.py").read_text(encoding="utf-8")

    assert 'explain(request.headers.get("X-Node-Token"' in gw.replace("_node_peers.", "node_peers.")
    assert "Invalid or missing node secret (" in gw, "the 401 must carry the reason"
    assert "pair again" in client, "the client's 401 must say how to recover"
