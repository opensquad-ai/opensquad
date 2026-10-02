"""A restart resumes the paired machines this agent was already using.

Starting fresh meant the peer bridge was rebuilt on the first remote call and a relay
subscription the owner had dropped was never re-asserted — the agent stayed silently cut
off from a group it is in. Boot now logs in to each paired machine and re-subscribes every
group it remembers there.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import opensquad.input_hub as input_hub_mod  # noqa: E402
import opensquad.peer_bridge as peer_bridge_mod  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    agent_dir = tmp_path / "agents" / "pm"
    agent_dir.mkdir(parents=True)
    # a real agent always has its config file; remember_peer() reads it (and refuses to
    # invent one when it is missing)
    (agent_dir / "config.json").write_text('{"agent_name": "pm"}', encoding="utf-8")
    monkeypatch.setattr(input_hub_mod.input_hub, "agent_dir", str(agent_dir))
    monkeypatch.setattr(peer_bridge_mod, "_config_path", lambda: str(agent_dir / "config.json"))
    return agent_dir


def _peer(host: str, group: str, *, with_account: bool = True) -> None:
    account = {"email": "b@ai", "password": "pw"} if with_account else {}
    peer_bridge_mod.remember_peer(host, f"http://{host}:9555", "tok", account=account)
    peer_bridge_mod.remember_peer_group(host, group)


def test_a_restart_logs_in_again_and_re_asserts_every_remembered_group(env, monkeypatch):
    _peer("192.168.5.4", "g-7f3a")
    peer_bridge_mod.remember_peer_group("192.168.5.4", "g-other")
    subs: list[tuple] = []
    monkeypatch.setattr(peer_bridge_mod, "peer_bridge", lambda host: (object(), ""))
    monkeypatch.setattr(
        peer_bridge_mod,
        "subscribe_group",
        lambda host, gid: subs.append((host, gid)) or {"ok": True},
    )

    summary = peer_bridge_mod.restore_peer_state()

    assert [p["host"] for p in summary["peers"]] == ["192.168.5.4"]
    assert summary["peers"][0]["logged_in"] is True
    assert sorted(subs) == [("192.168.5.4", "g-7f3a"), ("192.168.5.4", "g-other")]
    assert summary["peers"][0]["groups"] == {"g-7f3a": "subscribed", "g-other": "subscribed"}
    assert summary["errors"] == []


def test_a_peer_without_an_account_is_reported_not_guessed(env, monkeypatch):
    _peer("192.168.5.4", "g-7f3a", with_account=False)
    called: list[str] = []
    monkeypatch.setattr(peer_bridge_mod, "peer_bridge", lambda host: called.append(host) or (object(), ""))

    summary = peer_bridge_mod.restore_peer_state()

    assert called == []  # never logs in without credentials
    assert "register_account" in summary["peers"][0]["note"]


def test_a_broken_peer_does_not_stop_the_others(env, monkeypatch):
    _peer("192.168.5.4", "g-7f3a")
    _peer("10.0.0.9", "g-other")
    monkeypatch.setattr(
        peer_bridge_mod,
        "peer_bridge",
        lambda host: (None, "unreachable") if host == "192.168.5.4" else (object(), ""),
    )
    monkeypatch.setattr(peer_bridge_mod, "subscribe_group", lambda host, gid: {"ok": True})

    summary = peer_bridge_mod.restore_peer_state()

    assert len(summary["peers"]) == 2
    assert summary["peers"][0]["logged_in"] is False
    assert summary["peers"][1]["logged_in"] is True
    assert any("192.168.5.4" in e for e in summary["errors"])


def test_a_refused_subscription_is_reported(env, monkeypatch):
    _peer("192.168.5.4", "g-7f3a")
    monkeypatch.setattr(peer_bridge_mod, "peer_bridge", lambda host: (object(), ""))
    monkeypatch.setattr(peer_bridge_mod, "subscribe_group", lambda host, gid: {"ok": False, "error": "HTTP 401"})

    summary = peer_bridge_mod.restore_peer_state()

    assert summary["peers"][0]["groups"]["g-7f3a"] == "not_subscribed"
    assert any("HTTP 401" in e for e in summary["errors"])


def test_boot_actually_calls_the_restore():
    """The restore has to run at startup, or a restart still means starting from nothing."""
    src = (Path(__file__).resolve().parents[1] / "src" / "opensquad" / "agent_boot_phases.py").read_text(
        encoding="utf-8"
    )

    assert "restore_peer_state" in src
    at = src.index("restore_peer_state")
    assert "await asyncio.to_thread(restore_peer_state)" in src[at - 200 : at + 200]
    # after the home bridge is registered, so the restore can resolve this machine's gateway
    assert src.index("bridge_module.bridge = agent_bridge") < at


def test_the_summary_is_json_serialisable(env, monkeypatch):
    _peer("192.168.5.4", "g-7f3a")
    monkeypatch.setattr(peer_bridge_mod, "peer_bridge", lambda host: (object(), ""))
    monkeypatch.setattr(peer_bridge_mod, "subscribe_group", lambda host, gid: {"ok": True})

    json.dumps(peer_bridge_mod.restore_peer_state())
