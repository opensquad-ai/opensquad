"""Shared store for the gateway-to-gateway group relay (see docs/cross_machine_relay_design.md).

An agent has one socket, to its **own** gateway. When it joins a group that lives
on a paired machine, that machine owns the group's messages but the agent's socket
is not there, so nothing arrives. The relay closes that gap: the owning gateway
pushes the message to the agent's home gateway, which delivers it to the agent's
user over its existing socket.

This module is the part both processes need, because the two halves are written
from opposite ends:

* the **agent** (its process) subscribes — it asks the owning gateway to push, and
  records the secret its home gateway must expect on the way back;
* the **home gateway** (the backend process) receives — it verifies that secret.

Both run in the same workspace, so they share this file. Keeping it out of
``app/`` lets the agent import it without dragging in the gateway backend.

Store shape (``<workspace>/data/relay/relay_links.json``)::

    {"links": {
        "subscribers": {group_id: {"<callback>#<user>": {secret, host, user_id, added_at}}},
        "outbound":    {group_id: {host, secrets: [..], added_at}}
    }}

A group is keyed by ``callback_url`` **and** the home ``user_id``, because one
home gateway can host several agents that all subscribe to the same remote group
and each must be delivered to its own user.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from typing import Any

# One hop only: a relayed message is delivered and never forwarded again.
RELAY_MAX_HOPS = 1

# A relayed message is dropped when the same (origin_host, message_id) was already
# seen within this window — belt to the hop cap's braces.
DEDUP_TTL_S = 300.0

_FILE = "relay_links.json"

_LOCK = threading.Lock()


def store_file() -> str:
    from opensquad.system_config import syscfg

    return os.path.join(syscfg.workspace_data_dir("relay"), _FILE)


def _read() -> dict:
    try:
        with open(store_file(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data: dict) -> None:
    path = store_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _links(data: dict) -> dict:
    links = data.get("links")
    return links if isinstance(links, dict) else {}


def _sub_key(callback_url: str, user_id: str) -> str:
    return f"{str(callback_url or '').rstrip('/')}#{user_id or ''!s}"


# ── owner side: who wants this group pushed to them ────────────────────────


def subscribe(group_id: str, callback_url: str, secret: str, user_id: str = "", host: str = "") -> dict:
    """Record that ``callback_url`` wants messages for ``group_id`` pushed to it.

    ``secret`` is minted by the *subscriber* and echoed back on every push, so the
    owner never needs a credential of its own to reach the subscriber. ``user_id``
    is the subscriber's user on its own gateway — where the message must land.
    """
    if not group_id or not callback_url:
        return {"ok": False, "error": "group_id and callback_url are required"}
    with _LOCK:
        data = _read()
        links = _links(data)
        groups = links.get("subscribers")
        if not isinstance(groups, dict):
            groups = {}
        subs = groups.get(group_id)
        if not isinstance(subs, dict):
            subs = {}
        subs[_sub_key(callback_url, user_id)] = {
            "secret": str(secret or ""),
            "host": str(host or ""),
            "user_id": str(user_id or ""),
            "callback_url": str(callback_url or "").rstrip("/"),
            "added_at": time.time(),
        }
        groups[group_id] = subs
        links["subscribers"] = groups
        data["links"] = links
        _write(data)
    return {"ok": True, "group_id": group_id, "callback_url": callback_url, "user_id": str(user_id or "")}


def unsubscribe(group_id: str, callback_url: str = "", user_id: str = "") -> int:
    """Drop one subscriber (or every subscriber of a group). Returns how many went."""
    with _LOCK:
        data = _read()
        links = _links(data)
        groups = links.get("subscribers")
        if not isinstance(groups, dict) or group_id not in groups:
            return 0
        subs = groups.get(group_id)
        if not isinstance(subs, dict):
            return 0
        if callback_url:
            removed = 1 if subs.pop(_sub_key(callback_url, user_id), None) is not None else 0
        else:
            removed = len(subs)
            subs = {}
        if subs:
            groups[group_id] = subs
        else:
            groups.pop(group_id, None)
        links["subscribers"] = groups
        data["links"] = links
        _write(data)
        return removed


def subscribers(group_id: str) -> list[dict]:
    """Everyone who asked for ``group_id``: ``{callback_url, secret, user_id, host}``."""
    groups = _links(_read()).get("subscribers")
    if not isinstance(groups, dict):
        return []
    subs = groups.get(group_id)
    if not isinstance(subs, dict):
        return []
    out = []
    for entry in subs.values():
        if not isinstance(entry, dict):
            continue
        out.append(
            {
                "callback_url": str(entry.get("callback_url") or ""),
                "secret": str(entry.get("secret") or ""),
                "user_id": str(entry.get("user_id") or ""),
                "host": str(entry.get("host") or ""),
            }
        )
    return out


# ── home side: the secrets this gateway expects on inbound pushes ────────────


def remember_outbound(group_id: str, host: str, secret: str) -> dict:
    """Remember the secret minted for the subscription of ``group_id`` on ``host``.

    Several agents on this gateway can subscribe to the same remote group, so a
    group keeps a list of the secrets it should accept.
    """
    with _LOCK:
        data = _read()
        links = _links(data)
        out = links.get("outbound")
        if not isinstance(out, dict):
            out = {}
        entry = out.get(group_id)
        if not isinstance(entry, dict):
            entry = {"host": str(host or ""), "secrets": [], "added_at": time.time()}
        secrets_list = entry.get("secrets")
        if not isinstance(secrets_list, list):
            secrets_list = []
        if secret and secret not in secrets_list:
            secrets_list.append(str(secret))
        entry["secrets"] = secrets_list
        entry["host"] = str(host or entry.get("host") or "")
        out[group_id] = entry
        links["outbound"] = out
        data["links"] = links
        _write(data)
    return {"ok": True, "group_id": group_id, "host": host}


def forget_outbound(group_id: str, secret: str = "") -> bool:
    with _LOCK:
        data = _read()
        links = _links(data)
        out = links.get("outbound")
        if not isinstance(out, dict) or group_id not in out:
            return False
        entry = out.get(group_id)
        if secret and isinstance(entry, dict):
            secrets_list = [s for s in (entry.get("secrets") or []) if s != secret]
            if secrets_list:
                entry["secrets"] = secrets_list
                out[group_id] = entry
            else:
                out.pop(group_id, None)
        else:
            out.pop(group_id, None)
        links["outbound"] = out
        data["links"] = links
        _write(data)
        return True


def new_secret() -> str:
    """A fresh per-subscription secret (the home gateway mints it)."""
    return secrets.token_urlsafe(24)


def verify_inbound(group_id: str, secret: str) -> bool:
    """True when ``secret`` is one this gateway minted for ``group_id``."""
    out = _links(_read()).get("outbound")
    if not isinstance(out, dict):
        return False
    entry = out.get(group_id)
    if not isinstance(entry, dict):
        return False
    if not secret:
        return False
    for expected in entry.get("secrets") or []:
        if expected and secrets.compare_digest(str(expected), str(secret)):
            return True
    return False


# ── loop protection ────────────────────────────────────────────────────────

_seen: dict[tuple[str, str], float] = {}


def _prune(now: float) -> None:
    for key, at in list(_seen.items()):
        if now - at > DEDUP_TTL_S:
            _seen.pop(key, None)


def already_seen(origin_host: str, message_id: str) -> bool:
    """True when this relayed message was handled before (and records it now)."""
    if not message_id:
        return False
    key = (str(origin_host or ""), str(message_id))
    now = time.time()
    with _LOCK:
        _prune(now)
        if key in _seen:
            return True
        _seen[key] = now
    return False


def reset_seen() -> None:
    """Test helper: forget the dedup window."""
    with _LOCK:
        _seen.clear()


# ── envelope ───────────────────────────────────────────────────────────────


def build_envelope(group_id: str, message: dict, origin_host: str, user_id: str = "", hops: int = 1) -> dict:
    """The frame pushed to a subscriber. ``hops`` starts at 1 (first and only hop)."""
    return {
        "type": "message:relay",
        "group_id": group_id,
        "origin_host": str(origin_host or ""),
        "target_user_id": str(user_id or ""),
        "relay_hops": int(hops),
        "data": message,
    }


def within_hop_limit(envelope: dict) -> bool:
    """False when this envelope has already been relayed as far as it may go.

    Fail closed: a relayed envelope always carries a positive ``relay_hops``, so a
    missing or unparsable count means a non-conforming peer and is refused rather
    than trusted.
    """
    raw = envelope.get("relay_hops")
    if raw is None:
        return False
    try:
        hops = int(raw)
    except (TypeError, ValueError):
        return False
    return 1 <= hops <= RELAY_MAX_HOPS


def status() -> dict[str, Any]:
    """A read-only view for diagnostics: who we push to, what we expect inbound."""
    links = _links(_read())
    groups = links.get("subscribers") if isinstance(links.get("subscribers"), dict) else {}
    out = links.get("outbound") if isinstance(links.get("outbound"), dict) else {}
    return {
        "subscribers": {
            gid: [{"callback_url": s.get("callback_url"), "user_id": s.get("user_id")} for s in subs.values()]
            for gid, subs in groups.items()
            if isinstance(subs, dict)
        },
        "outbound": sorted(out.keys()),
        "max_hops": RELAY_MAX_HOPS,
    }
