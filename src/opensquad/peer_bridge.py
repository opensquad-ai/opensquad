"""Bridges to OTHER machines' gateways, keyed by host.

Pairing used to repoint the agent's only bridge at the machine it paired with,
which silently dropped it out of its own groups: one bridge, one ``base_url``, so
joining elsewhere meant leaving home. Peers are remembered instead —
``group_chat.peers[<host>]`` in ``config.json`` plus the token in the workspace
peer store — and a call aimed at a peer gets its **own** bridge. The home binding
is never touched.

Inbound is deliberately not part of this: a peer bridge holds no WebSocket, so
nothing is *received* from that gateway (that is the gateway relay project). While
that is missing, an agent that must receive on another machine should be a
dedicated agent living there — see the cross_machine_join skill.
"""

from __future__ import annotations

import json
import os
from typing import Any

_BRIDGES: dict[str, Any] = {}


def host_key(value: str) -> str:
    """Normalize a host, an authority or a base URL to one lookup key."""
    raw = str(value or "").strip().lower()
    for prefix in ("https://", "http://", "wss://", "ws://"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
    return raw.rstrip("/")


def _config_path() -> str:
    from opensquad.input_hub import input_hub

    agent_dir = getattr(input_hub, "agent_dir", "") or ""
    return os.path.join(agent_dir, "config.json") if agent_dir else ""


def load_peers() -> dict[str, Any]:
    """Peers this agent remembers (from its own ``config.json``)."""
    path = _config_path()
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        return {}
    chat = cfg.get("group_chat") if isinstance(cfg.get("group_chat"), dict) else {}
    peers = chat.get("peers") if isinstance(chat.get("peers"), dict) else {}
    return {k: v for k, v in peers.items() if isinstance(v, dict)}


def find_peer(host: str) -> dict[str, Any] | None:
    """The remembered peer for ``host`` — by host name or by gateway address."""
    wanted = host_key(host)
    if not wanted:
        return None
    for key, entry in load_peers().items():
        if host_key(key) == wanted or host_key(str(entry.get("base_url") or "")) == wanted:
            return entry
    return None


def peer_token(host: str) -> str:
    """The peer's token: from the config entry, else the workspace peer store."""
    entry = find_peer(host) or {}
    token = str(entry.get("token") or "")
    if token:
        return token
    try:
        from opensquad.node_peers import load_local_peer

        local = load_local_peer(str(entry.get("base_url") or host_key(host))) or {}
        return str(local.get("token") or "")
    except Exception:
        return ""


def remember_peer_group(host: str, group_id: str) -> bool:
    """Record that ``group_id`` lives on ``host``, a machine this agent paired with.

    Written when this agent joins a group on a peer. The board belongs to the
    machine that owns the group, and pairing no longer repoints the home bridge, so
    this is what lets a board call about that group be routed back to its owner:
    the group is noted *next to* the peer's address and token in
    ``group_chat.peers[<host>].groups``.
    """
    group = str(group_id or "").strip()
    if not group:
        return False
    path = _config_path()
    if not path:
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        if not isinstance(cfg, dict):
            return False
        chat = cfg.get("group_chat") if isinstance(cfg.get("group_chat"), dict) else {}
        peers = chat.get("peers") if isinstance(chat.get("peers"), dict) else {}
        wanted = host_key(host)
        entry = peers.get(wanted) if isinstance(peers.get(wanted), dict) else None
        if entry is None:
            entry = next(
                (v for v in peers.values() if isinstance(v, dict) and host_key(str(v.get("base_url") or "")) == wanted),
                None,
            )
        if entry is None:
            return False
        groups = entry.get("groups")
        if not isinstance(groups, list):
            groups = []
        if group not in [str(g) for g in groups]:
            groups.append(group)
        entry["groups"] = groups
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def peer_for_group(group_id: str) -> dict[str, Any] | None:
    """The remembered peer that owns ``group_id``, or ``None`` when it is local.

    A group on this machine is in no peer's ``groups`` list, so ``None`` means
    "keep it local" — never "unknown, guess".
    """
    group = str(group_id or "").strip()
    if not group:
        return None
    for entry in load_peers().values():
        groups = entry.get("groups")
        if isinstance(groups, list) and group in [str(g) for g in groups]:
            return entry
    return None


def remember_peer(
    host: str,
    base_url: str,
    token: str = "",
    name: str = "",
    account: dict[str, Any] | None = None,
) -> bool:
    """Record a peer in ``config.json`` without disturbing the home binding.

    Only ``group_chat.peers`` is written: ``group_chat.base_url``, the account and
    ``gateway`` keep pointing at this agent's own machine.
    """
    path = _config_path()
    if not path:
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        if not isinstance(cfg, dict):
            return False
        chat = cfg.get("group_chat") if isinstance(cfg.get("group_chat"), dict) else {}
        peers = chat.get("peers") if isinstance(chat.get("peers"), dict) else {}
        key = host_key(host) or host_key(base_url)
        if not key:
            return False
        entry = dict(peers.get(key) or {})
        entry["host"] = key
        if base_url:
            entry["base_url"] = str(base_url).rstrip("/")
        if token:
            entry["token"] = str(token)
        if name:
            entry["name"] = str(name)
        if account:
            entry["account"] = dict(account)
        peers[key] = entry
        chat["peers"] = peers
        cfg["group_chat"] = chat
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def peer_bridge(host: str) -> tuple[Any | None, str]:
    """A bridge for that peer's gateway, or ``(None, why-not)``.

    Cached per host, and never connected to a WebSocket: this is the outbound
    half only (join/send there), so the agent keeps receiving from home.
    """
    entry = find_peer(host)
    if not entry:
        return None, (
            f"Not paired with {host}. Run pair_with_node(invite='<host>:<port>#<group>?code=XXXXXX') "
            "on this machine first."
        )
    base_url = str(entry.get("base_url") or "").rstrip("/")
    if not base_url:
        return None, f"Peer {host} has no gateway address recorded; pair with it again."
    account = entry.get("account") if isinstance(entry.get("account"), dict) else {}
    email = str(account.get("email") or "")
    password = str(account.get("password") or "")
    if not email or not password:
        return None, (
            f"No account on {base_url} yet. Run im.register_account(email='<unique>@ai', "
            "password='...', host='" + host_key(host) + "') first."
        )

    key = host_key(base_url)
    cached = _BRIDGES.get(key)
    if cached is not None:
        return cached, ""

    try:
        from opensquad.bridge import ChatProBridge

        b = ChatProBridge(base_url=base_url, email=email, password=password)
        if not b.login():
            return None, f"Could not log in on {base_url} as {email}."
        _BRIDGES[key] = b
        return b, ""
    except Exception as exc:  # noqa: BLE001 - report, never crash the tool
        return None, f"Could not reach {base_url}: {exc}"
