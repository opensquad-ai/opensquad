"""Bridges to OTHER machines' gateways, keyed by host.

Pairing used to repoint the agent's only bridge at the machine it paired with,
which silently dropped it out of its own groups: one bridge, one ``base_url``, so
joining elsewhere meant leaving home. Peers are remembered instead —
``group_chat.peers[<host>]`` in ``config.json`` plus the token in the workspace
peer store — and a call aimed at a peer gets its **own** bridge. The home binding
is never touched.

Inbound: a paired machine pushes a group's messages to this agent's **own** gateway over
the relay (see ``docs/cross_machine_relay_design.md``), and that gateway delivers them to
the agent's socket — so a group joined over there is received here. At boot,
:func:`restore_peer_state` logs in to each peer again and re-asserts those subscriptions,
so a restart resumes them instead of starting from nothing.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

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


def _entry_stamp(entry: dict[str, Any]) -> float:
    """When this peer entry was written — the freshest wins when two resolve alike."""
    for field in ("paired_at", "added_at", "at"):
        try:
            value = float(entry.get(field) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value:
            return value
    return 0.0


def find_peer(host: str) -> dict[str, Any] | None:
    """The remembered peer for ``host`` — by host name or by gateway address.

    More than one entry can resolve to the same machine (an early pairing keyed by
    ``host:port`` beside a later one keyed by ``host``), so the **most recently
    paired** entry wins: returning the first match by insertion order let a stale
    entry shadow the fresh token, and the only symptom was a 401 from the peer.
    """
    wanted = host_key(host)
    if not wanted:
        return None
    matches = [
        entry
        for key, entry in load_peers().items()
        if host_key(key) == wanted or host_key(str(entry.get("base_url") or "")) == wanted
    ]
    if not matches:
        return None
    return max(matches, key=_entry_stamp)


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


def own_account_ids(host: str = "") -> set[str]:
    """This agent's account ids ON paired machines (all peers when ``host`` is empty).

    A relayed copy of a message this agent sent carries **that** machine's sender id,
    not our home ``bridge.user_id``, so the bridge's own-message filter cannot
    recognise it and the agent ends up answering itself. These are the ids it must
    filter as well.
    """
    wanted = host_key(host)
    out: set[str] = set()
    for key, entry in load_peers().items():
        if wanted and host_key(key) != wanted and host_key(str(entry.get("base_url") or "")) != wanted:
            continue
        account = entry.get("account") if isinstance(entry.get("account"), dict) else {}
        uid = str(account.get("user_id") or "")
        if uid:
            out.add(uid)
    return out


def forget_peer_group(host: str, group_id: str) -> bool:
    """Drop ``group_id`` from a peer's recorded list (after leaving it there)."""
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
        groups = [str(g) for g in (entry.get("groups") or []) if str(g) != group]
        entry["groups"] = groups
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def unsubscribe_group(host: str, group_id: str, timeout: float = 10.0) -> dict[str, Any]:
    """Tell ``host`` to stop pushing ``group_id`` here, and forget the local secret.

    Leaving a group on a peer otherwise leaves the peer pushing its messages at this
    machine for good: the subscription row on the owner's side is not tied to
    membership, and the token that authorised it stays valid. Idempotent — a peer
    that no longer has the row answers 200 with ``removed: 0``.
    """
    entry = find_peer(host)
    if not entry:
        return {"ok": False, "error": f"Not paired with {host}."}
    base_url = str(entry.get("base_url") or "").rstrip("/")
    token = peer_token(host)
    if not base_url or not token:
        return {"ok": False, "error": f"Peer {host} is missing its address or token; pair with it again."}

    user_id = ""
    try:
        import opensquad.bridge as bridge_module

        user_id = str(getattr(getattr(bridge_module, "bridge", None), "user_id", "") or "")
    except Exception:
        user_id = ""

    result: dict[str, Any] = {"ok": False}
    try:
        import requests

        resp = requests.delete(
            f"{base_url}/api/relay/subscribe",
            headers={"X-Node-Token": token},
            json={
                "group_id": str(group_id),
                "callback_url": _home_gateway_url(),
                "user_id": user_id,
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            body = resp.json() if resp.content else {}
            result = {"ok": True, "removed": int((body or {}).get("removed") or 0)}
        else:
            result = {"ok": False, "error": f"Unsubscribe on {base_url} failed (HTTP {resp.status_code})."}
    except Exception as exc:  # noqa: BLE001 - report, never crash the tool
        result = {"ok": False, "error": f"Could not reach {base_url}: {exc}"}

    # Forget the local half whatever the peer answered: a stale inbound secret would
    # let a push we no longer want be accepted, and if the owner never got the delete
    # its pushes now fail closed (401) instead of arriving.
    try:
        from opensquad import relay_link

        relay_link.forget_outbound(str(group_id))
    except Exception:
        pass
    forget_peer_group(host, str(group_id))
    return result


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
        # One machine, one entry. An earlier pairing could have left a twin keyed by
        # ``host:port`` next to this ``host``, and keeping both is what let a stale
        # token shadow the fresh one (find_peer used to take the first match). Merge
        # anything worth keeping, then drop the twin.
        authority = host_key(str(base_url or ""))
        entry = dict(peers.get(key) or {})
        for other in list(peers):
            if other == key:
                continue
            candidate = peers.get(other)
            if not isinstance(candidate, dict):
                continue
            same = host_key(other) == key or (authority and host_key(str(candidate.get("base_url") or "")) == authority)
            if not same:
                continue
            for field in ("account", "groups", "name"):
                if not entry.get(field) and candidate.get(field):
                    entry[field] = candidate[field]
            peers.pop(other, None)
        entry["host"] = key
        entry["paired_at"] = time.time()
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


def owner_bridge(group_id: str = "", collab_id: str = "") -> tuple[Any | None, str]:
    """The bridge that owns the group a chat/notification belongs to.

    Home by default; a group joined on a paired machine — or a collaboration task
    recorded as owned by one — gets that peer's own bridge, so an invitation or a
    task-assignment notice is posted to the group where it actually lives instead
    of to this agent's own gateway. ``(None, why-not)`` when the owner's bridge is
    not usable.
    """
    host = ""
    try:
        from opensquad.collab_board import board_owner

        host = board_owner(collab_id=collab_id, group_id=group_id)
    except Exception:
        host = ""
    if host:
        return peer_bridge(host)
    import opensquad.bridge as bridge_module

    home = getattr(bridge_module, "bridge", None)
    if home is None or not getattr(home, "token", ""):
        return None, "Bridge not logged in."
    return home, ""


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
        # Remember this agent's account id ON that machine. The peer relays the
        # group's messages back to us, including copies of our own — and that copy
        # carries *this* id as `sender_id`, not the home one, so the bridge needs to
        # know it to recognise its own message (see own_account_ids).
        if str(getattr(b, "user_id", "") or "") and str(b.user_id) != str(account.get("user_id") or ""):
            merged = dict(account)
            merged["user_id"] = str(b.user_id)
            remember_peer(host, base_url, account=merged)
        return b, ""
    except Exception as exc:  # noqa: BLE001 - report, never crash the tool
        return None, f"Could not reach {base_url}: {exc}"


def _home_gateway_url() -> str:
    """This machine's own gateway address — where the peer should push messages."""
    try:
        from opensquad.bridge import gateway_base_url

        url = gateway_base_url()
    except Exception:
        url = ""
    if not url:
        try:
            from opensquad.system_config import syscfg

            url = syscfg.gateway_http()
        except Exception:
            url = ""
    return str(url or "").rstrip("/")


def _local_agent_id() -> str:
    """This agent's id as the gateway's registry knows it (config ``agent_id``,
    else the agent directory's name)."""
    path = _config_path()
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        if isinstance(cfg, dict) and str(cfg.get("agent_id") or "").strip():
            return str(cfg["agent_id"]).strip()
    except Exception:
        pass
    return os.path.basename(os.path.dirname(path))


def restore_peer_state() -> dict[str, Any]:
    """Re-establish what this agent already had on paired machines.

    A restart used to mean "start from nothing": the peer bridge was rebuilt lazily on the
    first remote call, and a relay subscription the owner had dropped — a wiped store, a
    re-paired peer — was never re-asserted, so the agent stayed **silently** cut off from a
    group it is in. At boot this logs in to each paired machine (a stale token surfaces in
    the log now rather than on the first send) and re-asserts the subscription for every
    group it remembers there. The owner answering "already subscribed" is fine; the missing
    case is what this is for.

    Never raises: a restart must not fail because one peer is unreachable.
    """
    summary: dict[str, Any] = {"peers": [], "errors": []}
    try:
        peers = load_peers()
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(f"could not read peers: {exc}")
        return summary

    for host, entry in peers.items():
        account = entry.get("account") if isinstance(entry.get("account"), dict) else {}
        record: dict[str, Any] = {
            "host": host,
            "base_url": entry.get("base_url") or "",
            "logged_in": False,
            "groups": {},
        }
        if not account.get("email") or not account.get("password"):
            record["note"] = "no account on that machine; im.register_account(..., host=...) first"
            summary["peers"].append(record)
            continue

        bridge, why = peer_bridge(host)
        if bridge is None:
            record["note"] = why
            summary["errors"].append(f"{host}: {why}")
            summary["peers"].append(record)
            continue
        record["logged_in"] = True

        for group_id in [str(g) for g in (entry.get("groups") or []) if str(g)]:
            relay = subscribe_group(host, group_id)
            backfilled = int(relay.get("backfilled") or 0)
            record["groups"][group_id] = "subscribed" if relay.get("ok") else "not_subscribed"
            if backfilled:
                # The owner was holding frames for this machine while it was down, and
                # handed them over as part of the re-assert: worth saying in the boot
                # summary, because it is the difference between "up again" and "caught
                # up again".
                record["backfilled"] = int(record.get("backfilled") or 0) + backfilled
            if not relay.get("ok"):
                summary["errors"].append(f"{host}/{group_id}: {relay.get('error')}")
        summary["peers"].append(record)

    for peer in summary["peers"]:
        if peer.get("groups") or peer.get("note"):
            logger.info("[PeerBridge] restored %s: %s", peer["host"], peer)
    return summary


def subscribe_group(host: str, group_id: str, timeout: float = 10.0) -> dict[str, Any]:
    """Ask ``host`` to push ``group_id``'s messages to this machine's gateway.

    Called after this agent joins a group on a peer: the peer owns the group, but
    this agent's socket is at home, so the peer must be told where home is. The
    secret is minted here and recorded locally, so this gateway recognises the
    peer's pushes; the peer only echoes it back.
    """
    entry = find_peer(host)
    if not entry:
        return {"ok": False, "error": f"Not paired with {host}."}
    base_url = str(entry.get("base_url") or "").rstrip("/")
    token = peer_token(host)
    if not base_url or not token:
        return {"ok": False, "error": f"Peer {host} is missing its address or token; pair with it again."}
    callback_url = _home_gateway_url()
    if not callback_url:
        return {"ok": False, "error": "This machine's gateway address is unknown; cannot receive relayed messages."}

    import requests

    from opensquad import relay_link

    secret = relay_link.new_secret()
    # The home gateway must deliver to *this* agent's user, since the group itself
    # does not exist there — the message arrives as a personal delivery.
    user_id = ""
    try:
        import opensquad.bridge as bridge_module

        user_id = str(getattr(getattr(bridge_module, "bridge", None), "user_id", "") or "")
    except Exception:
        user_id = ""
    try:
        resp = requests.post(
            f"{base_url}/api/relay/subscribe",
            headers={"X-Node-Token": token},
            json={
                "group_id": str(group_id),
                "callback_url": callback_url,
                "secret": secret,
                "user_id": user_id,
                # So a task-window event can be delivered to this agent's control
                # channel at home (that is where the owning gateway dispatches it).
                "agent_id": _local_agent_id(),
            },
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - report, never crash the tool
        return {"ok": False, "error": f"Could not reach {base_url}: {exc}"}
    if resp.status_code != 200:
        hint = ""
        if resp.status_code == 401:
            hint = (
                f" The token stored for {host_key(host)} was refused — it is usually stale (from an "
                "earlier pairing). Run pair_with_node again with a fresh code."
            )
        return {"ok": False, "error": f"Subscribe on {base_url} failed (HTTP {resp.status_code}).{hint}"}

    # Bound to the user it was minted for: the owner echoes this secret back on
    # every push, and a push aimed at another local user is refused rather than
    # delivered (see relay_link.verify_inbound_user).
    relay_link.remember_outbound(str(group_id), host_key(host), secret, user_id=user_id)
    # (Re)subscribing also says "I am back": the owner hands over whatever it could not
    # deliver while this machine was away and answers with the count, so a reconnect
    # catches up at once instead of waiting for the owner's retry tick.
    backfilled = 0
    try:
        backfilled = int((resp.json() or {}).get("backfilled") or 0)
    except Exception:
        backfilled = 0
    return {
        "ok": True,
        "group_id": str(group_id),
        "callback_url": callback_url,
        "backfilled": backfilled,
    }
