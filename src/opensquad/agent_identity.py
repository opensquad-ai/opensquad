"""Which machine owns an @ai account.

Two machines configured with the same @ai email are one identity, not two agents:
they fight over the password (each login failure triggers a reset) and both answer
the same messages. The gateway therefore remembers which node first claimed an
email, and refuses a *different* node that tries to use it instead of silently
handing the account over.

Kept beside the other workspace data as a small JSON map (``email -> node uid``),
so no schema change is involved. An agent that sends no uid — every existing
install, and anything older — is never locked out: the check only applies once a
uid is actually presented.
"""

from __future__ import annotations

import os
import threading

_LOCK = threading.Lock()
_BINDINGS_FILE = "bindings.json"


def _key(email: str) -> str:
    return str(email or "").strip().lower()


def bindings_file() -> str:
    from opensquad.system_config import syscfg

    return os.path.join(syscfg.workspace_data_dir("agent_identity"), _BINDINGS_FILE)


def _read() -> dict:
    try:
        from opensquad._storage.json_io import read_json

        data = read_json(bindings_file(), {})
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data: dict) -> None:
    from opensquad._storage.json_io import atomic_write_json

    atomic_write_json(bindings_file(), data)


def uid_for(email: str) -> str:
    """The node uid that owns this email, or "" when nobody has claimed it."""
    return str(_read().get(_key(email), "") or "")


def check(email: str, uid: str) -> tuple[bool, str]:
    """``(allowed, owner)`` for this email + node uid.

    Allowed when nobody has claimed the email, when the caller sends no uid
    (legacy), or when the uid matches the claim.
    """
    claimed = uid_for(email)
    mine = str(uid or "").strip()
    if not claimed or not mine:
        return True, claimed
    return claimed == mine, claimed


def bind(email: str, uid: str) -> str:
    """Remember ``email -> uid`` on first claim; returns the effective owner.

    First claim wins: a second node cannot take over an email that is already
    bound, which is the whole point of the binding.
    """
    mine = str(uid or "").strip()
    if not mine:
        return uid_for(email)
    with _LOCK:
        data = _read()
        key = _key(email)
        if not data.get(key):
            data[key] = mine
            _write(data)
        return str(data.get(key) or mine)
