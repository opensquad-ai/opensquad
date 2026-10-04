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
import logging
import os
import secrets
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# One hop only: a relayed message is delivered and never forwarded again.
RELAY_MAX_HOPS = 1

# A relayed message is dropped when the same (origin_host, message_id) was already
# seen within this window — belt to the hop cap's braces.
#
# **Invariant: this window must outlive the outbox** (``DEDUP_TTL_S > OUTBOX_TTL_S``).
# A queued push is retried, and a push that actually arrived but whose answer was
# lost is retried too — the receiving gateway recognises that second copy only while
# the window is open. If the queue outlived the window, a late retry would be
# delivered twice. ``tests/test_relay_outbox.py`` asserts the invariant.
DEDUP_TTL_S = 900.0

_SUBSCRIBERS_FILE = "relay_subscribers.json"
_OUTBOUND_FILE = "relay_outbound.json"
_OUTBOX_FILE = "relay_outbox.json"

# ── the delivery queue ──────────────────────────────────────────────────────
#
# A push fails for reasons the peer cannot help: its gateway is restarting, its
# agent is offline, the LAN hiccuped. Counting the failure and dropping the frame
# loses the message for good — the group lives *here*, so the subscriber has no
# other way to learn about it (its own history fetch is the only recovery, and a
# task event or a DM is not in its history at all). So a failed push is written
# down, retried with a backoff, and flushed at once when the subscriber comes back.
OUTBOX_MAX_ATTEMPTS = 6
# Past this age an entry is dropped: the message is stale by then.
OUTBOX_TTL_S = 600.0
# A long outage of the only peer must not grow the file without bound.
OUTBOX_MAX_ENTRIES = 500
# First retry soon enough to feel immediate, then back off to the ceiling.
OUTBOX_BASE_DELAY_S = 20.0
OUTBOX_MAX_DELAY_S = 240.0
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


# ── owner side: pushes that failed and are waiting for a retry ───────────────


def outbox_file() -> str:
    """Gateway-owned: pushes that failed and are waiting for a retry.

    One writer per file, like the other two halves: the fan-out runs in the gateway
    process, so the gateway owns this file. It is on disk rather than in memory so a
    restart of *this* machine resumes the retries instead of losing them.
    """
    return os.path.join(store_dir(), _OUTBOX_FILE)


def _outbox_entries() -> dict:
    entries = _branch(outbox_file(), "outbox")
    return entries if isinstance(entries, dict) else {}


def _outbox_write(entries: dict) -> None:
    _write_path(outbox_file(), {"links": {"outbox": entries}})


def _outbox_key(group_id: str, callback_url: str, user_id: str, dedupe_id: str) -> str:
    return f"{group_id}|{_sub_key(callback_url, user_id)}|{dedupe_id}"


def _outbox_dedupe_id(envelope: dict) -> str:
    """The identity of the queued frame — what the receiver dedupes a retry by."""
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    return str(data.get("id") or data.get("event_id") or envelope.get("event_id") or "")


def _backoff_delay(attempts: int) -> float:
    """Seconds to wait after ``attempts`` failed attempts (first wait = the base).

    The curve is chosen to fit inside the TTL: with the defaults the waits are
    20+40+80+160+240 = 540s, so the last of ``OUTBOX_MAX_ATTEMPTS`` attempts lands
    before the entry expires. ``tests/test_relay_outbox.py`` holds that sum to the TTL.
    """
    step = max(0, min(int(attempts) - 1, 8))
    return min(OUTBOX_BASE_DELAY_S * (2**step), OUTBOX_MAX_DELAY_S)


def _prune_outbox(entries: dict, now: float) -> dict:
    """Drop what is too old or too much. Loudly: these are messages going missing."""
    for key, entry in list(entries.items()):
        first = float((entry or {}).get("first_at") or 0.0)
        if first and now - first > OUTBOX_TTL_S:
            logger.warning(
                "[Relay] dropping an undelivered %s message (queued %.0fs, %s attempts): %s",
                (entry or {}).get("group_id"),
                now - first,
                (entry or {}).get("attempts"),
                (entry or {}).get("last_error"),
            )
            entries.pop(key, None)
    overflow = len(entries) - OUTBOX_MAX_ENTRIES
    if overflow > 0:
        oldest = sorted(entries.items(), key=lambda kv: float((kv[1] or {}).get("first_at") or 0.0))
        for key, entry in oldest[:overflow]:
            logger.warning(
                "[Relay] outbox over %d entries; dropping the oldest undelivered %s message",
                OUTBOX_MAX_ENTRIES,
                (entry or {}).get("group_id"),
            )
            entries.pop(key, None)
    return entries


def enqueue_outbox(
    *,
    group_id: str,
    envelope: dict,
    callback_url: str,
    user_id: str = "",
    error: str = "",
    now: float | None = None,
) -> dict:
    """Remember a push that failed, so it can be retried.

    Keyed by the message as well as the subscriber, so re-queueing the same frame
    refreshes one entry instead of stacking a second copy of it.

    A frame with no message id is **not** queued: the receiver dedupes a retry by that
    id, so retrying without one could deliver twice. It is logged instead.
    """
    if not group_id or not callback_url or not isinstance(envelope, dict):
        return {"ok": False, "error": "group_id, callback_url and envelope are required"}
    dedupe_id = _outbox_dedupe_id(envelope)
    if not dedupe_id:
        logger.warning("[Relay] not queueing an undelivered %s push with no message id: %s", group_id, error)
        return {"ok": False, "error": "no_message_id"}
    at = time.time() if now is None else float(now)
    with _LOCK:
        entries = _outbox_entries()
        key = _outbox_key(group_id, callback_url, user_id, dedupe_id)
        existing = entries.get(key) if isinstance(entries.get(key), dict) else {}
        entries[key] = {
            "group_id": str(group_id),
            "callback_url": str(callback_url or "").rstrip("/"),
            "user_id": str(user_id or ""),
            "dedupe_id": dedupe_id,
            "kind": str(envelope.get("type") or "message:relay"),
            "envelope": envelope,
            "first_at": float(existing.get("first_at") or at),
            "last_at": at,
            "attempts": int(existing.get("attempts") or 0),
            # The failed push just counted as an attempt, so the first retry is due now.
            "next_at": at,
            "last_error": str(error or ""),
        }
        entries = _prune_outbox(entries, at)
        _outbox_write(entries)
        queued = len(entries)
    return {"ok": True, "key": key, "queued": queued}


def outbox_entries() -> list[dict]:
    """Everything queued, oldest first (diagnostics and the retry loop)."""
    out = []
    for key, entry in _outbox_entries().items():
        if isinstance(entry, dict):
            out.append({**entry, "key": key})
    out.sort(key=lambda e: float(e.get("first_at") or 0.0))
    return out


def due_outbox(now: float | None = None, limit: int = 0) -> list[dict]:
    """Queued pushes ready for another attempt — not backing off, not expired."""
    at = time.time() if now is None else float(now)
    out = [
        entry
        for entry in outbox_entries()
        if float(entry.get("next_at") or 0.0) <= at
        and int(entry.get("attempts") or 0) < OUTBOX_MAX_ATTEMPTS
        and at - float(entry.get("first_at") or 0.0) <= OUTBOX_TTL_S
    ]
    return out[:limit] if limit and limit > 0 else out


def record_outbox_attempt(key: str, error: str = "", now: float | None = None) -> dict:
    """Count one failed retry and schedule the next — or give up on the entry."""
    at = time.time() if now is None else float(now)
    with _LOCK:
        entries = _outbox_entries()
        entry = entries.get(key)
        if not isinstance(entry, dict):
            return {"ok": False, "error": "unknown entry"}
        attempts = int(entry.get("attempts") or 0) + 1
        entry["attempts"] = attempts
        entry["last_at"] = at
        entry["last_error"] = str(error or "")
        if attempts >= OUTBOX_MAX_ATTEMPTS:
            logger.warning(
                "[Relay] giving up on an undelivered %s message after %d attempts: %s",
                entry.get("group_id"),
                attempts,
                error,
            )
            entries.pop(key, None)
            _outbox_write(entries)
            return {"ok": True, "attempts": attempts, "dropped": True}
        entry["next_at"] = at + _backoff_delay(attempts)
        entries[key] = entry
        _outbox_write(entries)
        return {"ok": True, "attempts": attempts, "next_at": entry["next_at"]}


def drop_outbox(key: str) -> bool:
    """Forget a queued push: it was delivered, or nobody is subscribed to it any more."""
    with _LOCK:
        entries = _outbox_entries()
        if key not in entries:
            return False
        entries.pop(key, None)
        _outbox_write(entries)
        return True


def outbox_size() -> int:
    """How many pushes are waiting (diagnostics)."""
    return len(_outbox_entries())


def prune_outbox(now: float | None = None) -> int:
    """Drop expired (and over-cap) entries from the file. Returns how many went.

    Expiry is enforced where the queue is read, but something has to *remove* the
    entry, or a peer that never comes back leaves its stale frames in the file
    forever. The retry loop calls this on every pass.
    """
    at = time.time() if now is None else float(now)
    with _LOCK:
        entries = _outbox_entries()
        before = len(entries)
        entries = _prune_outbox(entries, at)
        gone = before - len(entries)
        if gone:
            _outbox_write(entries)
        return gone


def clear_outbox() -> None:
    """Test helper: empty the queue."""
    with _LOCK:
        _outbox_write({})


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


def already_seen(origin_host: str, message_id: str, *, record: bool = True) -> bool:
    """True when this relayed message was handled before.

    ``record=True`` (the default) also remembers it — that is the dedup window. Pass
    ``record=False`` to *peek* instead, and call :func:`note_seen` once the message was actually
    delivered: recording before delivery turned a transient miss into a permanent loss, because
    the owner retries a push the receiver had already marked as seen, and every retry came back
    "duplicate" while the event never reached the agent.
    """
    if not message_id:
        return False
    key = (str(origin_host or ""), str(message_id))
    now = time.time()
    with _LOCK:
        _prune(now)
        if key in _seen:
            return True
        if record:
            _seen[key] = now
    return False


def note_seen(origin_host: str, message_id: str) -> None:
    """Remember a relayed message as handled (call it only after a successful delivery)."""
    if not message_id:
        return
    key = (str(origin_host or ""), str(message_id))
    with _LOCK:
        _seen[key] = time.time()


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
    queued = outbox_entries()
    return {
        "subscribers": {
            gid: [{"callback_url": s.get("callback_url"), "user_id": s.get("user_id")} for s in entries.values()]
            for gid, entries in groups.items()
            if isinstance(entries, dict)
        },
        "outbound": sorted(outbound.keys()),
        "max_hops": RELAY_MAX_HOPS,
        # Undelivered pushes waiting for a retry: the number to watch when a paired
        # machine has been off (a value that only grows is a peer that is not coming
        # back, and the entries expire rather than accumulate).
        "outbox": {
            "queued": len(queued),
            "oldest_age_s": (round(time.time() - float(queued[0].get("first_at") or 0.0), 1) if queued else 0.0),
            "max_attempts": OUTBOX_MAX_ATTEMPTS,
            "ttl_s": OUTBOX_TTL_S,
        },
    }
