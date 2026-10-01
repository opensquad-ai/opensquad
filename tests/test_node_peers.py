"""Pairing a peer node, and the scope of the token it gets."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from opensquad import node_peers as peers  # noqa: E402
from opensquad.system_config import syscfg  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(syscfg, "workspace_data_dir", lambda *parts: str(tmp_path / "_".join(parts)))
    return peers


def test_a_pairing_code_is_short_lived_and_single_use(store):
    started = store.start_pairing()
    assert len(started["code"]) == 6 and started["code"].isdigit()

    first = store.request_pairing(started["code"], name="machine-b")
    assert first["ok"] is True

    # the code is consumed by the first request
    again = store.request_pairing(started["code"], name="machine-c")
    assert again["ok"] is False
    assert again["status"] == "bad_code"


def test_a_wrong_code_is_refused_and_rate_limited(store):
    store.start_pairing()

    for _ in range(store.MAX_CODE_ATTEMPTS_PER_MINUTE):
        assert store.request_pairing("000000", name="guesser")["status"] == "bad_code"

    blocked = store.request_pairing("000000", name="guesser")
    assert blocked["status"] == "rate_limited"


def test_approving_mints_a_scoped_token_that_is_returned_once(store):
    code = store.start_pairing()["code"]
    request_id = store.request_pairing(code, name="machine-b")["request_id"]

    approved = store.decide_pairing(request_id, approve=True, decided_by="owner")
    assert approved["status"] == "approved"
    token = approved["token"]

    # the peer polls and gets the token a single time
    polled = store.pair_status(request_id)
    assert polled["status"] == "approved"
    assert polled["token"] == token
    assert store.pair_status(request_id).get("token") is None

    peer = store.verify(token)
    assert peer is not None
    assert peer["name"] == "machine-b"


def test_the_token_carries_fixed_scopes_and_none_of_the_dangerous_ones(store):
    code = store.start_pairing()["code"]
    request_id = store.request_pairing(code, name="machine-b")["request_id"]
    peer = store.verify(store.decide_pairing(request_id, approve=True)["token"])

    assert store.has_scope(peer, "agent:register")
    assert store.has_scope(peer, "board:write")
    # the two surfaces a peer must never reach
    assert not store.has_scope(peer, "auth:reset-password")
    assert not store.has_scope(peer, "launcher:admin")
    assert peer["scopes"] == peers.AGENT_SCOPES


def test_rejecting_leaves_no_peer(store):
    code = store.start_pairing()["code"]
    request_id = store.request_pairing(code, name="machine-b")["request_id"]

    assert store.decide_pairing(request_id, approve=False)["status"] == "rejected"
    assert store.list_peers() == []
    assert store.verify("anything") is None


def test_a_revoked_token_stops_working(store):
    code = store.start_pairing()["code"]
    request_id = store.request_pairing(code, name="machine-b")["request_id"]
    approved = store.decide_pairing(request_id, approve=True)
    token = approved["token"]

    assert store.revoke(approved["peer_id"]) is True
    assert store.verify(token) is None
    # revoking twice is not an error, just a no-op
    assert store.revoke(approved["peer_id"]) is False
    assert store.list_peers()[0]["revoked"] is True


def test_deciding_twice_is_refused(store):
    code = store.start_pairing()["code"]
    request_id = store.request_pairing(code, name="machine-b")["request_id"]
    store.decide_pairing(request_id, approve=True)

    second = store.decide_pairing(request_id, approve=True)
    assert second["ok"] is False
    assert second["status"] == "approved"


def test_pending_requests_are_listable_for_the_owner(store):
    code = store.start_pairing()["code"]
    store.request_pairing(code, name="machine-b")

    pending = store.list_requests()
    assert [p["name"] for p in pending] == ["machine-b"]


def test_an_unknown_token_or_request_is_simply_not_found(store):
    assert store.verify("nope") is None
    assert store.pair_status("pair_missing") == {"status": "unknown"}
