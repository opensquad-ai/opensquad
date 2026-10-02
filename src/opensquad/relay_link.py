"""The relay's bookkeeping: subscriptions, inbound secrets, loop protection.

An agent has one socket, to its **own** gateway. When it joins a group that lives
on a paired machine, that machine owns the group's messages but the agent's socket
is not there, so nothing arrives. The relay closes that gap: the owning gateway
pushes the message to the agent's home gateway, which delivers it to the agent's
user over its existing socket.

**Two files, one writer each.** The two halves are written from opposite ends, and
they sit in the same workspace but are written by *different processes*:

* the **gateway** process writes ``relay_subscribers.json`` — who wants a group
  pushed to them, and the secret they minted (``subscribe`` / ``unsubscribe``);
* the **agent** process writes ``relay_outbound.json`` — the secrets this machine
  minted for the subscriptions it created elsewhere (``remember_outbound``).

They used to share one file, which meant read-whole-file / write-whole-file from
two processes guarded only by a ``threading.Lock``: a last-writer-wins window that
silently dropped the other side's entry (and surfaced later as an unexplained 401
on the next push, long after the join reported success). One file per writer
removes the window by construction; reads (``subscribers``, ``verify_inbound``,
``status``) still cross processes freely, because every write is an atomic rename.

File shapes::

    relay_subscribers.json  {"links": {"subscribers": {group_id: {"<callback>#<user>": {...}}}}}
    relay_outbound.json     {"links": {"outbound": {group_id: {"host": ..., "secrets": [...]}}}}

A group is keyed by ``callback_url`` **and** the home ``user_id``, because one home
gateway can host several agents that all subscribe to the same remote group and
each must be delivered to its own user.
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

_SUBSCRIBERS_FILE = "relay_subscribers.json"
_OUTBOUND_FILE = "relay_outbound.json"
# What both branches shared before the split, and the lock that keeps the one-time
# migration from running twice at once.
_LEGACY_FILE = "relay_links.json"
_MIGRATION_LOCK = "relay_migrate.lock"
# A migration lock left behind by a killed process must not block the split
# forever; past this age it is treated as stale and cleared.
_MIGRATION_LOCK_STALE_S = 60.0

_LOCK = threading.Lock()


def store_dir() -> str:
    """The directory both files live in (workspace ``data/relay/``)."""
    from opensquad.system_config import syscfg

    return syscfg.workspace_data_dir("relay")


def subscribers_file() -> str:
    """Gateway-owned: who to push a group's messages to."""
    return os.path.join(store_dir(), _SUBSCRIBERS_FILE)


def outbound_file() -> str:
    """Agent-owned: the secrets this machine minted for its own subscriptions."""
    return os.path.join(store_dir(), _OUTBOUND_FILE)


def store_file() -> str:
    """Compatibility alias for the subscribers half (what the old file held)."""
    return subscribers_file()


def _legacy_file() -> str:
    return os.path.join(store_dir(), _LEGACY_FILE)


def _read_path(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_path(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _branch(path: str, name: str) -> dict:
    links = _read_path(path).get("links")
    branch = links.get(name) if isinstance(links, dict) else None
    return branch if isinstance(branch, dict) else {}


def _migrate_once() -> None:
    """Move a legacy combined file's branches into the per-writer files.

    Guarded by an ``O_EXCL`` lock file — the only cross-process lock here, and only
    for this one-time move: if another process is migrating, skip, because reads
    fall back to the legacy file until the move is done, so nothing is lost. The
    legacy file is renamed rather than deleted, so a bad migration stays diagnosable.
    """
    legacy = _legacy_file()
    if not os.path.isfile(legacy):
        return
    lock = os.path.join(store_dir(), _MIGRATION_LOCK)
    try:
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # Held by someone. If its holder died, the split would never happen (reads
        # keep falling back to the legacy file, so nothing is lost, but the
        # lost-update window would stay open) — clear a stale lock and retry once.
        try:
            if time.time() - os.path.getmtime(lock) <= _MIGRATION_LOCK_STALE_S:
                return  # another process is migrating; the legacy fallback covers us
            os.unlink(lock)
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except Exception:
            return
    except Exception:
        return
    try:
        os.close(fd)
        links = _read_path(legacy).get("links")
        links = links if isinstance(links, dict) else {}
        subs = links.get("subscribers") if isinstance(links.get("subscribers"), dict) else None
        out = links.get("outbound") if isinstance(links.get("outbound"), dict) else None
        if subs and not os.path.isfile(subscribers_file()):
            _write_path(subscribers_file(), {"links": {"subscribers": subs}})
        if out and not os.path.isfile(outbound_file()):
            _write_path(outbound_file(), {"links": {"outbound": out}})
        os.replace(legacy, f"{legacy}.migrated")
    except Exception:
        pass
    finally:
        try:
            os.unlink(lock)
        except OSError:
            pass


def _sub_key(callback_url: str, user_id: str) -> str:
    return f"{str(callback_url or '').rstrip('/')}#{user_id or ''!s}"


# ── owner side: who wants this group pushed to them ────────────────────────


def subscribe(
    group_id: str,
    callback_url: str,
    secret: str,
    user_id: str = "",
    host: str = "",
    peer_id: str = "",
    agent_id: str = "",
) -> dict:
    """Record that ``callback_url`` wants messages for ``group_id`` pushed to it.

    ``secret`` is minted by the *subscriber* and echoed back on every push, so the
    owner never needs a credential of its own to reach the subscriber. ``user_id``
    is the subscriber's user on its own gateway — where the message must land.
    ``peer_id`` is the paired machine that asked, so revoking it can drop its rows.
    ``agent_id`` is the agent there, which is what a *task* event is delivered to
    (those travel on the agent's control channel, not the group chat socket).
    """
    if not group_id or not callback_url:
        return {"ok": False, "error": "group_id and callback_url are required"}
    with _LOCK:
        _migrate_once()
        path = subscribers_file()
        data = _read_path(path)
        links = data.get("links") if isinstance(data.get("links"), dict) else {}
        groups = links.get("subscribers")
        if not isinstance(groups, dict):
            groups = {}
        subs = groups.get(group_id)
        if not isinstance(subs, dict):
            subs = {}
        subs[_sub_key(callback_url, user_id)] = {
            "secret": str(secret or ""),
            "host": str(host or ""),
            "peer_id": str(peer_id or ""),
            "agent_id": str(agent_id or ""),
            "user_id": str(user_id or ""),
            "callback_url": str(callback_url or "").rstrip("/"),
            "added_at": time.time(),
        }
        groups[group_id] = subs
        links["subscribers"] = groups
        data["links"] = links
        _write_path(path, data)
    return {"ok": True, "group_id": group_id, "callback_url": callback_url, "user_id": str(user_id or "")}


def unsubscribe(group_id: str, callback_url: str = "", user_id: str = "") -> int:
    """Drop one subscriber (or every subscriber of a group). Returns how many went."""
    with _LOCK:
        _migrate_once()
        path = subscribers_file()
        data = _read_path(path)
        links = data.get("links") if isinstance(data.get("links"), dict) else {}
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
        _write_path(path, data)
        return removed


def unsubscribe_peer(peer_id: str) -> int:
    """Drop every subscription a revoked peer created. Returns how many went.

    Revoking a peer stops it authenticating, but the rows it created keep being
    pushed to — so revocation is not a complete shut-off until they go too.
    """
    wanted = str(peer_id or "")
    if not wanted:
        return 0
    with _LOCK:
        _migrate_once()
        path = subscribers_file()
        data = _read_path(path)
        links = data.get("links") if isinstance(data.get("links"), dict) else {}
        groups = links.get("subscribers")
        if not isinstance(groups, dict):
            return 0
        removed = 0
        for gid in list(groups):
            subs = groups.get(gid)
            if not isinstance(subs, dict):
                continue
            keep = {k: v for k, v in subs.items() if str((v or {}).get("peer_id") or "") != wanted}
            removed += len(subs) - len(keep)
            if keep:
                groups[gid] = keep
            else:
                groups.pop(gid, None)
        if not removed:
            return 0
        links["subscribers"] = groups
        data["links"] = links
        _write_path(path, data)
        return removed


def subscribers(group_id: str) -> list[dict]:
    """Everyone who asked for ``group_id``: ``{callback_url, secret, user_id, host}``."""
    _migrate_once()
    groups = _branch(subscribers_file(), "subscribers") or _branch(_legacy_file(), "subscribers")
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
                "agent_id": str(entry.get("agent_id") or ""),
            }
        )
    return out


# ── home side: the secrets this gateway expects on inbound pushes ────────────


def _entry_secrets(entry: Any) -> list[dict]:
    """Normalize ``secrets``: new entries are objects, legacy ones are strings."""
    if not isinstance(entry, dict):
        return []
    raw = entry.get("secrets")
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, str):
            out.append({"secret": item, "user_id": ""})
        elif isinstance(item, dict) and item.get("secret"):
            out.append({"secret": str(item["secret"]), "user_id": str(item.get("user_id") or "")})
    return out


def _outbound_entry(group_id: str) -> dict:
    _migrate_once()
    out = _branch(outbound_file(), "outbound") or _branch(_legacy_file(), "outbound")
    entry = out.get(group_id)
    return entry if isinstance(entry, dict) else {}


def remember_outbound(group_id: str, host: str, secret: str, user_id: str = "") -> dict:
    """Remember the secret minted for the subscription of ``group_id`` on ``host``.

    Several agents on this gateway can subscribe to the same remote group, so a
    group keeps a list of the secrets it accepted — each **bound to the user it was
    minted for**, so a push cannot be redirected at another local user.
    """
    with _LOCK:
        _migrate_once()
        path = outbound_file()
        data = _read_path(path)
        links = data.get("links") if isinstance(data.get("links"), dict) else {}
        out = links.get("outbound")
        if not isinstance(out, dict):
            out = {}
        entry = out.get(group_id)
        if not isinstance(entry, dict):
            entry = {"host": str(host or ""), "secrets": [], "added_at": time.time()}
        secrets_list = _entry_secrets(entry)
        if secret and not any(item["secret"] == str(secret) for item in secrets_list):
            secrets_list.append({"secret": str(secret), "user_id": str(user_id or "")})
        entry["secrets"] = secrets_list
        entry["host"] = str(host or entry.get("host") or "")
        out[group_id] = entry
        links["outbound"] = out
        data["links"] = links
        _write_path(path, data)
    return {"ok": True, "group_id": group_id, "host": host}


def forget_outbound(group_id: str, secret: str = "") -> bool:
    """Drop one secret, or the whole subscription for ``group_id`` when none is given.

    The no-secret case is what leaving a group uses, and it used to be unreachable:
    the comprehension that filtered the list kept every entry when the secret was
    empty, so the group was never actually forgotten.
    """
    with _LOCK:
        _migrate_once()
        path = outbound_file()
        data = _read_path(path)
        links = data.get("links") if isinstance(data.get("links"), dict) else {}
        out = links.get("outbound")
        if not isinstance(out, dict) or group_id not in out:
            return False
        if not secret:
            out.pop(group_id, None)
        else:
            entry = out.get(group_id)
            secrets_list = [item for item in _entry_secrets(entry) if item["secret"] != str(secret)]
            if secrets_list:
                entry["secrets"] = secrets_list
                out[group_id] = entry
            else:
                out.pop(group_id, None)
        links["outbound"] = out
        data["links"] = links
        _write_path(path, data)
        return True


def outbound_host(group_id: str) -> str:
    """The peer host this machine minted a subscription for ``group_id`` on.

    A relayed message's ``/uploads/...`` references live on that machine, so this is
    what tells the receiving gateway where to point them.
    """
    return str(_outbound_entry(group_id).get("host") or "")


def new_secret() -> str:
    """A fresh per-subscription secret (the home gateway mints it)."""
    return secrets.token_urlsafe(24)


def verify_inbound(group_id: str, secret: str) -> bool:
    """True when ``secret`` is one this gateway minted for ``group_id``."""
    if not secret:
        return False
    for expected in _entry_secrets(_outbound_entry(group_id)):
        if expected["secret"] and secrets.compare_digest(expected["secret"], str(secret)):
            return True
    return False


def verify_inbound_user(group_id: str, secret: str) -> str:
    """The user a minted secret is bound to — ``""`` when unknown or unbound.

    Empty means either the secret is not ours (callers check :func:`verify_inbound`
    first) or an older subscription was recorded before the binding existed; both
    are accepted as before, so a rollout cannot reject legitimate pushes.
    """
    if not secret:
        return ""
    for expected in _entry_secrets(_outbound_entry(group_id)):
        if expected["secret"] and secrets.compare_digest(expected["secret"], str(secret)):
            return expected["user_id"]
    return ""


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


def build_envelope(
    group_id: str,
    message: dict,
    origin_host: str,
    user_id: str = "",
    hops: int = 1,
    kind: str = "message:relay",
    target_agent_id: str = "",
) -> dict:
    """The frame pushed to a subscriber. ``hops`` starts at 1 (first and only hop).

    ``kind`` says what is being relayed: a group message (delivered to the chat
    socket) or a task-window event (delivered to the agent's control channel, which
    is where the owning gateway dispatches it locally too).
    """
    envelope = {
        "type": str(kind or "message:relay"),
        "group_id": group_id,
        "origin_host": str(origin_host or ""),
        "target_user_id": str(user_id or ""),
        "relay_hops": int(hops),
        "data": message,
    }
    if target_agent_id:
        envelope["target_agent_id"] = str(target_agent_id)
    return envelope


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
    groups = _branch(subscribers_file(), "subscribers") or _branch(_legacy_file(), "subscribers")
    outbound = _branch(outbound_file(), "outbound") or _branch(_legacy_file(), "outbound")
    return {
        "subscribers": {
            gid: [{"callback_url": s.get("callback_url"), "user_id": s.get("user_id")} for s in entries.values()]
            for gid, entries in groups.items()
            if isinstance(entries, dict)
        },
        "outbound": sorted(outbound.keys()),
        "max_hops": RELAY_MAX_HOPS,
    }
