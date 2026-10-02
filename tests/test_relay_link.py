"""The relay's shared store: subscriptions, inbound secrets, loop protection.

The gateway pushes a group's messages to a paired machine; the receiving gateway
delivers them to the agent's socket. These cover the bookkeeping both halves share
— who subscribes, which secret is expected inbound, and the one-hop dedup — without
standing up two gateways.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import opensquad.relay_link as rl  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "store_file", lambda: str(tmp_path / "relay" / "relay_links.json"))
    rl.reset_seen()
    return tmp_path / "relay" / "relay_links.json"


def test_subscribing_records_who_to_push_to(store):
    rl.subscribe("g-7f3a", "http://home-a:9555", "sekret", user_id="u-1", host="machine-b")

    subs = rl.subscribers("g-7f3a")

    assert len(subs) == 1
    assert subs[0]["callback_url"] == "http://home-a:9555"
    assert subs[0]["secret"] == "sekret"
    assert subs[0]["user_id"] == "u-1"


def test_two_agents_on_one_home_gateway_both_subscribe(store):
    """Same callback URL, different users: both must be kept, not overwritten."""
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")
    rl.subscribe("g-7f3a", "http://home-a:9555", "s2", user_id="u-2")

    subs = rl.subscribers("g-7f3a")

    assert sorted(s["user_id"] for s in subs) == ["u-1", "u-2"]


def test_unsubscribing_one_leaves_the_other(store):
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")
    rl.subscribe("g-7f3a", "http://home-a:9555", "s2", user_id="u-2")

    removed = rl.unsubscribe("g-7f3a", "http://home-a:9555", user_id="u-1")

    assert removed == 1
    assert [s["user_id"] for s in rl.subscribers("g-7f3a")] == ["u-2"]


def test_a_group_with_no_subscribers_pushes_to_nobody(store):
    assert rl.subscribers("g-nobody") == []


def test_inbound_is_accepted_only_for_a_secret_we_minted(store):
    secret = rl.new_secret()
    rl.remember_outbound("g-7f3a", "machine-b", secret)

    assert rl.verify_inbound("g-7f3a", secret)
    assert not rl.verify_inbound("g-7f3a", "not-the-secret")
    assert not rl.verify_inbound("g-other", secret)


def test_a_second_agent_adds_its_secret_without_dropping_the_first(store):
    s1 = rl.new_secret()
    s2 = rl.new_secret()
    rl.remember_outbound("g-7f3a", "machine-b", s1)
    rl.remember_outbound("g-7f3a", "machine-b", s2)

    assert rl.verify_inbound("g-7f3a", s1)
    assert rl.verify_inbound("g-7f3a", s2)


def test_forgetting_one_secret_keeps_the_rest(store):
    s1 = rl.new_secret()
    s2 = rl.new_secret()
    rl.remember_outbound("g-7f3a", "machine-b", s1)
    rl.remember_outbound("g-7f3a", "machine-b", s2)

    rl.forget_outbound("g-7f3a", s1)

    assert not rl.verify_inbound("g-7f3a", s1)
    assert rl.verify_inbound("g-7f3a", s2)


def test_the_same_relayed_message_is_seen_only_once(store):
    assert not rl.already_seen("machine-b", "m_1")
    assert rl.already_seen("machine-b", "m_1")


def test_different_origins_do_not_collide(store):
    assert not rl.already_seen("machine-b", "m_1")
    assert not rl.already_seen("machine-c", "m_1")


def test_a_message_without_an_id_is_never_deduped(store):
    assert not rl.already_seen("machine-b", "")
    assert not rl.already_seen("machine-b", "")


def test_the_envelope_carries_the_origin_and_one_hop(store):
    env = rl.build_envelope("g-7f3a", {"id": "m_1"}, "machine-b", user_id="u-1")

    assert env["type"] == "message:relay"
    assert env["group_id"] == "g-7f3a"
    assert env["origin_host"] == "machine-b"
    assert env["target_user_id"] == "u-1"
    assert env["relay_hops"] == 1


def test_one_hop_is_the_limit(store):
    assert rl.within_hop_limit({"relay_hops": 1})
    assert not rl.within_hop_limit({"relay_hops": 2})
    # A missing/garbage hop count is treated as beyond the limit (fail closed).
    assert not rl.within_hop_limit({})
    assert not rl.within_hop_limit({"relay_hops": "x"})


def test_status_reports_both_directions(store):
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")
    rl.remember_outbound("g-local", "machine-b", "s2")

    view = rl.status()

    assert view["subscribers"]["g-7f3a"][0]["user_id"] == "u-1"
    assert view["outbound"] == ["g-local"]


def test_the_store_is_readable_json_on_disk(store):
    rl.subscribe("g-7f3a", "http://home-a:9555", "s1", user_id="u-1")

    data = json.loads(store.read_text(encoding="utf-8"))

    assert "g-7f3a" in data["links"]["subscribers"]
