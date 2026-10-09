"""`im.list_groups` must include the groups the agent joined on paired machines.

Reported: an agent asked which groups it was in and named only its home group ("开发协作组",
g-default), even though it had joined a group on a paired machine. Nothing was wrong with the
join — `im.list_groups` simply asked one gateway (its own) and never looked at the groups
recorded in its own config under `group_chat.peers[<host>].groups`.

The point of the fix, and of the first test below: **membership is local knowledge.** The peer
being down must not hide the fact that this agent is in its group — that is the state the agent
most needs to see. Metadata (name/description) does come from the owner, so it stays empty when
the owner cannot be reached.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad import peer_bridge  # noqa: E402
from opensquad.tools import im  # noqa: E402


class FakeBridge:
    """A gateway that answers `GET /api/groups`."""

    def __init__(self, groups, error: str = ""):
        self.groups = groups
        self.error = error
        self.calls = 0

    def list_groups_api(self):
        self.calls += 1
        if self.error:
            raise RuntimeError(self.error)
        return self.groups


@pytest.fixture
def local(monkeypatch):
    bridge = FakeBridge([{"id": "g-default", "name": "开发协作组", "description": "local team"}])
    monkeypatch.setattr(im, "_bridge", lambda: bridge)
    return bridge


@pytest.fixture
def peers(monkeypatch):
    """Install the remembered-peer table, and the bridges the peers answer with."""
    table: dict = {}
    bridges: dict = {}

    monkeypatch.setattr(peer_bridge, "load_peers", lambda: table)

    def _peer_bridge(host: str):
        value = bridges.get(host)
        if value is None:
            return None, f"Not paired with {host}."
        if isinstance(value, str):  # an unreachable machine
            return None, value
        return value, ""

    monkeypatch.setattr(peer_bridge, "peer_bridge", _peer_bridge)
    return SimpleNamespace(table=table, bridges=bridges)


def test_a_peer_group_is_listed_even_when_that_machine_is_unreachable(local, peers):
    """The reported hole: the agent could not see the remote group at all."""
    peers.table["192.168.5.4"] = {"host": "192.168.5.4", "base_url": "http://192.168.5.4:9555", "groups": ["g-7f3a"]}
    peers.bridges["192.168.5.4"] = "connection refused"

    result = im.list_groups()

    assert result["status"] == "success"
    by_id = {g["id"]: g for g in result["groups"]}
    assert set(by_id) == {"g-default", "g-7f3a"}
    remote = by_id["g-7f3a"]
    assert remote["source"] == "remote" and remote["host"] == "192.168.5.4"
    assert remote["reachable"] is False
    assert remote["name"] == "", "its name lives on the machine that owns the group"
    assert "connection refused" in remote["note"]
    # …and the local group is still reported normally
    assert by_id["g-default"]["source"] == "local" and by_id["g-default"]["name"] == "开发协作组"


def test_a_reachable_peer_contributes_names_and_descriptions(local, peers):
    peers.table["192.168.5.4"] = {"host": "192.168.5.4", "groups": ["g-7f3a"]}
    peers.bridges["192.168.5.4"] = FakeBridge([{"id": "g-7f3a", "name": "远程协作组", "description": "on the peer"}])

    result = im.list_groups()

    remote = {g["id"]: g for g in result["groups"]}["g-7f3a"]
    assert remote["reachable"] is True
    assert remote["name"] == "远程协作组" and remote["description"] == "on the peer"
    assert "note" not in remote


def test_a_peer_that_stopped_listing_a_remembered_group_says_so(local, peers):
    """A stale record must not read like a healthy subscription."""
    peers.table["192.168.5.4"] = {"host": "192.168.5.4", "groups": ["g-gone"]}
    peers.bridges["192.168.5.4"] = FakeBridge([])

    result = im.list_groups()

    remote = {g["id"]: g for g in result["groups"]}["g-gone"]
    assert remote["reachable"] is False
    assert "does not list this group any more" in remote["note"]


def test_an_id_that_is_both_local_and_remote_appears_once(local, peers):
    peers.table["192.168.5.4"] = {"host": "192.168.5.4", "groups": ["g-default"]}
    peers.bridges["192.168.5.4"] = FakeBridge([{"id": "g-default", "name": "elsewhere", "description": ""}])

    result = im.list_groups()

    rows = [g for g in result["groups"] if g["id"] == "g-default"]
    assert len(rows) == 1 and rows[0]["source"] == "local"


def test_include_remote_false_is_local_only(local, peers):
    peers.table["192.168.5.4"] = {"host": "192.168.5.4", "groups": ["g-7f3a"]}
    peers.bridges["192.168.5.4"] = FakeBridge([{"id": "g-7f3a", "name": "远程协作组", "description": ""}])

    result = im.list_groups(include_remote=False)

    assert [g["id"] for g in result["groups"]] == ["g-default"]


def test_peers_without_remembered_groups_are_not_contacted(local, peers, monkeypatch):
    """A paired machine with no shared group must not cost a network round trip."""
    peers.table["192.168.9.9"] = {"host": "192.168.9.9", "groups": []}
    contacted: list[str] = []

    def _spy(host: str):
        contacted.append(host)
        return None, "should not be asked"

    monkeypatch.setattr(peer_bridge, "peer_bridge", _spy)

    result = im.list_groups()

    assert [g["id"] for g in result["groups"]] == ["g-default"]
    assert contacted == []


def test_host_still_asks_that_one_machine(local, peers):
    """The explicit form keeps its old meaning: that machine's list, nothing merged."""
    peers.table["192.168.5.4"] = {"host": "192.168.5.4", "groups": ["g-7f3a"]}
    peer = FakeBridge([{"id": "g-7f3a", "name": "远程协作组", "description": "on the peer"}])
    peers.bridges["192.168.5.4"] = peer

    result = im.list_groups(host="192.168.5.4")

    assert [g["id"] for g in result["groups"]] == ["g-7f3a"]
    assert "source" not in result["groups"][0], "the explicit form reports the payload as-is"
    assert peer.calls == 1


def test_host_not_ready_is_still_an_error(local, peers):
    result = im.list_groups(host="10.0.0.9")

    assert result["status"] == "error" and result["code"] == "peer_not_ready"


def test_no_local_groups_and_no_peers_is_an_empty_success(local, peers):
    local.groups = []

    result = im.list_groups()

    assert result == {"status": "success", "count": 0, "groups": []}
