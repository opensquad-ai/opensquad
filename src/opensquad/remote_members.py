"""Which paired machine an @ai account was created from.

A remote agent registers its account on the host through the host's
``POST /api/auth/register``, presenting the scoped peer token it got from pairing.
The host verifies that token but, until now, discarded the peer identity — so the
member list could not tell a remotely-joined agent from a local one.

This records that one fact, keyed by email, when the account is created. It is a
small JSON map (``email -> {peer_id, peer_name}``) kept beside the other workspace
data, so no schema change is involved — the same trade-off as
``opensquad.agent_identity``.

An account with no record is simply not remote: the lookup fails closed, and every
account that existed before this feature keeps showing as local.
"""

from __future__ import annotations

import os
import threading

_LOCK = threading.Lock()
_FILE = "remote_members.json"


def _key(email: str) -> str:
    return str(email or "").strip().lower()


def store_file() -> str:
    from opensquad.system_config import syscfg

    return os.path.join(syscfg.workspace_data_dir("remote_members"), _FILE)


def _read() -> dict:
    try:
        from opensquad._storage.json_io import read_json

        data = read_json(store_file(), {})
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data: dict) -> None:
    from opensquad._storage.json_io import atomic_write_json

    atomic_write_json(store_file(), data)


def remember(email: str, peer_id: str, peer_name: str = "") -> dict:
    """Record that ``email`` was registered from the paired machine ``peer_id``.

    Idempotent: a repeated registration (e.g. a password re-register) refreshes
    the stored name rather than stacking entries.
    """
    key = _key(email)
    if not key:
        return {}
    entry = {"peer_id": str(peer_id or ""), "peer_name": str(peer_name or "")}
    with _LOCK:
        data = _read()
        data[key] = entry
        _write(data)
    return entry


def get(email: str) -> dict:
    """The recorded origin for ``email``, or ``{}`` when it is not remote."""
    entry = _read().get(_key(email))
    return entry if isinstance(entry, dict) else {}


def is_remote(email: str) -> bool:
    return bool(get(email).get("peer_id"))


def label(email: str) -> str:
    """The origin machine's name for ``email``, or ``""`` when unknown."""
    return str(get(email).get("peer_name") or "")


def all_origins() -> dict:
    """The whole map, for the cached member-info lookup."""
    return _read()
