"""Invite strings: parsing, building, and joining through one.

The string lets someone on machine B join a group hosted on machine A without
transcribing an IP, a port and a group id by hand. A wrong gateway, a private
group and a malformed string each have to say something useful — they are the
three ways this actually goes wrong in the field.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import opensquad.bridge as bridge_mod  # noqa: E402
from opensquad.invite import build_invite, parse_invite  # noqa: E402
from opensquad.tools import invite as invite_tool  # noqa: E402

# ── parsing ────────────────────────────────────────────────────────────────


def test_a_plain_invite_parses():
    parsed = parse_invite("192.168.5.4#g-7f3a")

    assert parsed["host"] == "192.168.5.4"
    assert parsed["port"] == 9555  # the default, so the short form is enough
    assert parsed["group_id"] == "g-7f3a"
    assert parsed["code"] == ""
    assert parsed["base_url"] == "http://192.168.5.4:9555"


def test_an_explicit_port_and_code_parse():
    parsed = parse_invite("192.168.5.4:9600#g-7f3a?code=AB12CD")

    assert parsed["port"] == 9600
    assert parsed["code"] == "AB12CD"


def test_a_secure_host_is_reported_as_https():
    parsed = parse_invite("https://chat.example.com#g-7f3a")

    assert parsed["scheme"] == "https"
    assert parsed["base_url"] == "https://chat.example.com:9555"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "192.168.5.4",  # no group
        "#g-7f3a",  # no host
        "192.168.5.4#",  # no group id
        "192.168.5.4:not-a-port#g-7f3a",
        "192.168.5.4:99999#g-7f3a",
        "192.168.5.4#g 7f3a",  # group ids have no spaces
    ],
)
def test_a_bad_string_is_refused(bad):
    assert parse_invite(bad) is None


def test_build_and_parse_round_trip():
    invite = build_invite("192.168.5.4", "g-7f3a", port=9600, code="AB12CD")

    parsed = parse_invite(invite)

    assert (parsed["host"], parsed["port"], parsed["group_id"], parsed["code"]) == (
        "192.168.5.4",
        9600,
        "g-7f3a",
        "AB12CD",
    )


# ── joining ────────────────────────────────────────────────────────────────


class _Bridge:
    def __init__(self, *, joined: bool, detail: str = "", base_url: str = "http://192.168.5.4:9555"):
        self.token = "t"
        self.base_url = base_url
        self._joined = joined
        self._detail = detail

    def join_group_api(self, group_id):
        if self._joined:
            return {"ok": True}
        return {"ok": False, "detail": self._detail}


@pytest.fixture()
def http(monkeypatch):
    """Route by URL; record what was asked."""
    calls: list[str] = []
    status = {"join_request": 200}

    class _Response:
        def __init__(self, code):
            self.status_code = code
            self.content = b"{}"

        def json(self):
            return {"request_id": "jr_1"}

    def _post(url, **kwargs):
        calls.append(url)
        return _Response(status["join_request"])

    # pair_with_node / join_by_invite import requests at call time, so patching
    # the module attribute is what takes effect.
    import requests as real_requests

    monkeypatch.setattr(real_requests, "post", _post)
    return {"calls": calls, "status": status}


@pytest.fixture(autouse=True)
def _isolate_peer_state(tmp_path, monkeypatch):
    """These tests must not touch the live workspace: joining a peer writes the
    agent's config (peer entry) and the relay store, and both exist for real on this
    machine."""
    import opensquad.input_hub as input_hub_mod
    import opensquad.peer_bridge as peer_bridge_mod
    import opensquad.relay_link as relay_link

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "config.json").write_text('{"agent_name": "test"}', encoding="utf-8")
    monkeypatch.setattr(input_hub_mod.input_hub, "agent_dir", str(agent_dir))
    monkeypatch.setattr(peer_bridge_mod, "_config_path", lambda: str(agent_dir / "config.json"))
    monkeypatch.setattr(relay_link, "store_dir", lambda: str(tmp_path / "relay"))


def test_a_public_group_joins_directly(monkeypatch, http):
    monkeypatch.setattr(bridge_mod, "bridge", _Bridge(joined=True))

    res = invite_tool.join_by_invite("192.168.5.4#g-7f3a")

    assert res["status"] == "success"
    assert res["joined"] is True
    assert http["calls"] == []  # no request needed


def test_a_private_group_becomes_a_request(monkeypatch, http):
    monkeypatch.setattr(bridge_mod, "bridge", _Bridge(joined=False, detail="Cannot join private group"))

    res = invite_tool.join_by_invite("192.168.5.4#g-7f3a", note="B 机的 coder")

    assert res["status"] == "pending"
    assert res["request_id"] == "jr_1"
    assert any("/join-request" in url for url in http["calls"])
    assert "owner" in res["message"]


def test_another_failure_is_reported_not_misread(monkeypatch, http):
    monkeypatch.setattr(bridge_mod, "bridge", _Bridge(joined=False, detail="Group not found"))

    res = invite_tool.join_by_invite("192.168.5.4#g-7f3a")

    assert res["status"] == "error"
    assert http["calls"] == []  # only a *private* refusal becomes a request


def test_an_invite_for_an_unpaired_machine_asks_to_pair_not_to_repoint(monkeypatch, http):
    """The old answer told the operator to point ``group_chat.base_url`` at the
    invite and restart — which cost them their own groups. Another machine's invite
    now means "pair with it"."""
    monkeypatch.setattr(bridge_mod, "bridge", _Bridge(joined=True, base_url="http://10.0.0.9:9555"))

    res = invite_tool.join_by_invite("192.168.5.4#g-7f3a")

    assert res["status"] == "error"
    assert res["code"] == "not_paired"
    assert "pair_with_node" in res["message"]
    assert "group_chat.base_url" not in res["message"]


def test_an_invite_for_a_paired_machine_uses_that_peers_own_bridge(monkeypatch, http):
    """The home bridge is not consulted (and not repointed): the join happens
    through the peer's bridge, so the agent stays in its own groups."""
    import opensquad.peer_bridge as peer_bridge_mod

    home = _Bridge(joined=True, base_url="http://127.0.0.1:9555")
    peer = _Bridge(joined=True, base_url="http://192.168.5.4:9555")
    monkeypatch.setattr(bridge_mod, "bridge", home)
    monkeypatch.setattr(peer_bridge_mod, "peer_bridge", lambda host: (peer, ""))
    monkeypatch.setattr(peer_bridge_mod, "find_peer", lambda host: {"base_url": "http://192.168.5.4:9555"})

    res = invite_tool.join_by_invite("192.168.5.4#g-7f3a")

    assert res["status"] == "success"
    assert res["host"] == "192.168.5.4"
    assert home.token == "t"  # touched, not replaced


def test_joining_a_peer_group_remembers_which_machine_owns_it(monkeypatch, http):
    """The group is on the peer, so its board is too. Joining records that, which
    is what lets a collaboration task started here route its board calls back."""
    import opensquad.peer_bridge as peer_bridge_mod

    home = _Bridge(joined=True, base_url="http://127.0.0.1:9555")
    peer = _Bridge(joined=True, base_url="http://192.168.5.4:9555")
    recorded: list[tuple] = []
    monkeypatch.setattr(bridge_mod, "bridge", home)
    monkeypatch.setattr(peer_bridge_mod, "peer_bridge", lambda host: (peer, ""))
    monkeypatch.setattr(peer_bridge_mod, "find_peer", lambda host: {"base_url": "http://192.168.5.4:9555"})
    monkeypatch.setattr(
        peer_bridge_mod, "remember_peer_group", lambda host, group: recorded.append((host, group)) or True
    )

    res = invite_tool.join_by_invite("192.168.5.4#g-7f3a")

    assert res["status"] == "success"
    assert recorded == [("192.168.5.4", "g-7f3a")]


def test_joining_a_home_group_records_nothing(monkeypatch, http):
    """A group on this machine is not on any peer: nothing to remember."""
    import opensquad.peer_bridge as peer_bridge_mod

    home = _Bridge(joined=True, base_url="http://127.0.0.1:9555")
    recorded: list[tuple] = []
    monkeypatch.setattr(bridge_mod, "bridge", home)
    monkeypatch.setattr(
        peer_bridge_mod, "remember_peer_group", lambda host, group: recorded.append((host, group)) or True
    )

    res = invite_tool.join_by_invite("127.0.0.1:9555#g-home")

    assert res["status"] == "success"
    assert recorded == []


def test_a_peer_join_subscribes_to_the_relay(monkeypatch, http):
    """Regression: joining by invite recorded the board owner but never subscribed to
    the relay, so the membership was send-only and nothing ever came back."""
    import opensquad.peer_bridge as peer_bridge_mod

    home = _Bridge(joined=True, base_url="http://127.0.0.1:9555")
    peer = _Bridge(joined=True, base_url="http://192.168.5.4:9555")
    seen: list[tuple] = []
    monkeypatch.setattr(bridge_mod, "bridge", home)
    monkeypatch.setattr(peer_bridge_mod, "peer_bridge", lambda host: (peer, ""))
    monkeypatch.setattr(peer_bridge_mod, "find_peer", lambda host: {"base_url": "http://192.168.5.4:9555"})
    monkeypatch.setattr(
        peer_bridge_mod, "remember_peer_group", lambda host, gid: seen.append(("group", host, gid)) or True
    )
    monkeypatch.setattr(
        peer_bridge_mod, "subscribe_group", lambda host, gid: seen.append(("relay", host, gid)) or {"ok": True}
    )

    res = invite_tool.join_by_invite("192.168.5.4#g-7f3a")

    assert res["status"] == "success"
    assert res["relay"] == "subscribed"
    assert seen == [("group", "192.168.5.4", "g-7f3a"), ("relay", "192.168.5.4", "g-7f3a")]


def test_already_a_member_still_subscribes(monkeypatch, http):
    """The 400 "already a member" answer used to be reported as a failure, which
    skipped the subscription entirely — the exact shape of the field bug."""
    import opensquad.peer_bridge as peer_bridge_mod

    peer = _Bridge(joined=False, detail="Already a member", base_url="http://192.168.5.4:9555")
    monkeypatch.setattr(bridge_mod, "bridge", _Bridge(joined=True, base_url="http://127.0.0.1:9555"))
    monkeypatch.setattr(peer_bridge_mod, "peer_bridge", lambda host: (peer, ""))
    monkeypatch.setattr(peer_bridge_mod, "find_peer", lambda host: {"base_url": "http://192.168.5.4:9555"})
    monkeypatch.setattr(peer_bridge_mod, "remember_peer_group", lambda host, gid: True)
    monkeypatch.setattr(peer_bridge_mod, "subscribe_group", lambda host, gid: {"ok": True})

    res = invite_tool.join_by_invite("192.168.5.4#g-7f3a")

    assert res["status"] == "success"
    assert res["already_member"] is True
    assert res["relay"] == "subscribed"


def test_a_refused_subscription_is_reported_not_hidden(monkeypatch, http):
    import opensquad.peer_bridge as peer_bridge_mod

    peer = _Bridge(joined=True, base_url="http://192.168.5.4:9555")
    monkeypatch.setattr(bridge_mod, "bridge", _Bridge(joined=True, base_url="http://127.0.0.1:9555"))
    monkeypatch.setattr(peer_bridge_mod, "peer_bridge", lambda host: (peer, ""))
    monkeypatch.setattr(peer_bridge_mod, "find_peer", lambda host: {"base_url": "http://192.168.5.4:9555"})
    monkeypatch.setattr(peer_bridge_mod, "remember_peer_group", lambda host, gid: True)
    monkeypatch.setattr(
        peer_bridge_mod,
        "subscribe_group",
        lambda host, gid: {"ok": False, "error": "Subscribe on http://192.168.5.4:9555 failed (HTTP 401). stale"},
    )

    res = invite_tool.join_by_invite("192.168.5.4#g-7f3a")

    assert res["status"] == "success" and res["joined"] is True
    assert res["relay"] == "not_subscribed"
    assert "will\nNOT" not in res["message"] and "NOT be delivered" in res["message"]


def test_a_malformed_invite_says_what_it_wants(monkeypatch):
    res = invite_tool.join_by_invite("not-an-invite")

    assert res["status"] == "error"
    assert "<host>[:port]#<group-id>" in res["message"]


def test_the_tool_is_registered_for_agents():
    from opensquad import agents_boot

    assert agents_boot.TOOL_MODULES["invite"] == "opensquad.tools.invite"


def test_the_join_tools_reach_an_agent_that_has_no_tool_config():
    """Regression: the namespace was reachable only through config.json's tools
    list, so an agent created before it existed never registered it — the
    cross_machine_join skill loaded, and the tools it tells the agent to call did
    not exist. Mandatory is what makes the skill work on existing agents."""
    from opensquad import agents_boot

    assert "invite" in agents_boot.MANDATORY_TOOLS
    assert "invite" in agents_boot.BOOT_PHASES.build_tool_name_list({})


def test_the_namespace_exposes_both_join_tools():
    from opensquad.registry import ToolRegistry
    from opensquad.tools import invite as invite_tools

    registry = ToolRegistry()
    registry.register(invite_tools, "invite", level="extended")
    names = {t["function"]["name"] for t in registry.generate_openai_tools()}
    assert {"invite__pair_with_node", "invite__join_by_invite"} <= names
