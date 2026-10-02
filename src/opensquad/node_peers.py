"""Pairing another machine, and the scoped token it gets in return.

A second machine joins by asking, not by sharing `node_secret`: the host shows a
short-lived code, the peer submits it along with its name, the owner approves in
the UI, and the peer receives a token whose **scopes are fixed here** — it can
register its agents and work on the board, and it can never reset passwords or
touch the launcher. Tokens are stored hashed, so a stolen file does not hand out
working credentials, and a peer can be revoked without disturbing anyone else.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_FILE = "peers.json"

CODE_TTL_SECONDS = 300
MAX_CODE_ATTEMPTS_PER_MINUTE = 5

# What a paired peer may do. Deliberately excludes ``auth:reset-password`` and
# everything under the launcher: those are the two surfaces a peer must never
# reach, and they are the reason pairing exists instead of sharing node_secret.
AGENT_SCOPES = ("agent:register", "group:join", "board:read", "board:write")


def _now() -> float:
    return time.time()


def store_file() -> str:
    from opensquad.system_config import syscfg

    return os.path.join(syscfg.workspace_data_dir("node_peers"), _FILE)


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


def _hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


# ── the host side ──────────────────────────────────────────────────────────


def start_pairing() -> dict:
    """Show a code a peer can use for the next few minutes."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    with _LOCK:
        data = _read()
        data["pairing"] = {"code": code, "expires_at": _now() + CODE_TTL_SECONDS, "attempts": []}
        _write(data)
    return {"code": code, "expires_in": CODE_TTL_SECONDS}


def _code_ok(data: dict, code: str) -> bool:
    pairing = data.get("pairing") if isinstance(data.get("pairing"), dict) else {}
    if not pairing or str(pairing.get("code")) != str(code or "").strip():
        return False
    return float(pairing.get("expires_at") or 0) > _now()


def _record_attempt(data: dict) -> bool:
    """False when this caller has been guessing too often."""
    pairing = data.get("pairing") if isinstance(data.get("pairing"), dict) else {}
    attempts = [t for t in (pairing.get("attempts") or []) if _now() - float(t) < 60]
    attempts.append(_now())
    pairing["attempts"] = attempts
    data["pairing"] = pairing
    return len(attempts) <= MAX_CODE_ATTEMPTS_PER_MINUTE


def request_pairing(code: str, name: str = "") -> dict:
    """A peer asks to be paired, proving it knows the current code."""
    with _LOCK:
        data = _read()
        if not _code_ok(data, code):
            # Count the attempt BEFORE answering, so guessing is rate limited.
            allowed = _record_attempt(data)
            _write(data)
            if not allowed:
                return {"ok": False, "status": "rate_limited"}
            return {"ok": False, "status": "bad_code"}

        request_id = f"pair_{secrets.token_hex(6)}"
        requests = data.get("requests") if isinstance(data.get("requests"), dict) else {}
        requests[request_id] = {
            "id": request_id,
            "name": str(name or "").strip()[:120] or "unnamed node",
            "requested_at": _now(),
            "status": "pending",
        }
        data["requests"] = requests
        data["pairing"] = {}  # one code, one request
        _write(data)
    return {"ok": True, "status": "pending", "request_id": request_id}


def list_requests() -> list[dict]:
    data = _read()
    requests = data.get("requests") if isinstance(data.get("requests"), dict) else {}
    return [r for r in requests.values() if isinstance(r, dict) and r.get("status") == "pending"]


def pair_status(request_id: str) -> dict:
    """Polled by the peer: pending, or approved with its token (returned once)."""
    data = _read()
    requests = data.get("requests") if isinstance(data.get("requests"), dict) else {}
    request = requests.get(request_id)
    if not isinstance(request, dict):
        return {"status": "unknown"}
    if request.get("status") == "approved" and request.get("token"):
        token = str(request["token"])
        with _LOCK:
            fresh = _read()
            fresh_requests = fresh.get("requests") if isinstance(fresh.get("requests"), dict) else {}
            entry = fresh_requests.get(request_id)
            if isinstance(entry, dict):
                entry.pop("token", None)  # deliver it exactly once
                fresh["requests"] = fresh_requests
                _write(fresh)
        return {"status": "approved", "token": token, "scopes": list(AGENT_SCOPES)}
    return {"status": str(request.get("status") or "pending")}


def decide_pairing(request_id: str, *, approve: bool, decided_by: str = "") -> dict:
    """The owner answers. Approving mints the peer's token (returned once)."""
    with _LOCK:
        data = _read()
        requests = data.get("requests") if isinstance(data.get("requests"), dict) else {}
        request = requests.get(request_id)
        if not isinstance(request, dict):
            return {"ok": False, "status": "unknown"}
        if request.get("status") != "pending":
            return {"ok": False, "status": request.get("status")}

        if not approve:
            request["status"] = "rejected"
            request["decided_by"] = decided_by
            requests[request_id] = request
            data["requests"] = requests
            _write(data)
            return {"ok": True, "status": "rejected"}

        token = secrets.token_urlsafe(32)
        peer_id = f"peer_{secrets.token_hex(6)}"
        peers = data.get("peers") if isinstance(data.get("peers"), dict) else {}
        peers[peer_id] = {
            "id": peer_id,
            "name": request.get("name") or "unnamed node",
            "token_hash": _hash(token),
            "scopes": list(AGENT_SCOPES),
            "created_at": _now(),
            "last_seen_at": None,
            "revoked_at": None,
        }
        request["status"] = "approved"
        request["peer_id"] = peer_id
        request["token"] = token
        request["decided_by"] = decided_by
        requests[request_id] = request
        data["requests"] = requests
        data["peers"] = peers
        _write(data)
    return {"ok": True, "status": "approved", "peer_id": peer_id, "token": token, "scopes": list(AGENT_SCOPES)}


def list_peers() -> list[dict]:
    data = _read()
    peers = data.get("peers") if isinstance(data.get("peers"), dict) else {}
    return [
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "scopes": p.get("scopes") or [],
            "created_at": p.get("created_at"),
            "last_seen_at": p.get("last_seen_at"),
            "revoked": bool(p.get("revoked_at")),
        }
        for p in peers.values()
        if isinstance(p, dict)
    ]


def revoke(peer_id: str) -> bool:
    with _LOCK:
        data = _read()
        peers = data.get("peers") if isinstance(data.get("peers"), dict) else {}
        peer = peers.get(peer_id)
        if not isinstance(peer, dict) or peer.get("revoked_at"):
            return False
        peer["revoked_at"] = _now()
        peers[peer_id] = peer
        data["peers"] = peers
        _write(data)
    # Revocation has to be a complete shut-off, not only an auth change: the
    # subscriptions this peer created live in the relay store and would otherwise
    # keep being pushed to forever.
    try:
        from opensquad import relay_link

        removed = relay_link.unsubscribe_peer(peer_id)
        if removed:
            logger.info("[NodePeers] Revoked %s: dropped %d relay subscription(s)", peer_id, removed)
    except Exception:
        logger.debug("[NodePeers] Revoked %s: relay cleanup skipped", peer_id, exc_info=True)
    return True


# ── the peer side / the gateway's check ────────────────────────────────────


def verify(token: str) -> dict | None:
    """The peer this token belongs to, or None (unknown or revoked)."""
    if not token:
        return None
    digest = _hash(token)
    data = _read()
    peers = data.get("peers") if isinstance(data.get("peers"), dict) else {}
    for peer in peers.values():
        if not isinstance(peer, dict) or peer.get("revoked_at"):
            continue
        if secrets.compare_digest(str(peer.get("token_hash") or ""), digest):
            return {
                "id": peer.get("id"),
                "name": peer.get("name"),
                "scopes": tuple(peer.get("scopes") or ()),
            }
    return None


def has_scope(peer: dict | None, scope: str) -> bool:
    return bool(peer) and scope in tuple(peer.get("scopes") or ())


# ── this machine's own side: which hosts it is paired with ─────────────────
_LOCAL_FILE = "peer_tokens.json"


def local_file() -> str:
    from opensquad.system_config import syscfg

    return os.path.join(syscfg.workspace_data_dir("node_peers"), _LOCAL_FILE)


def _read_local() -> dict:
    try:
        with open(local_file(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_local(data: dict) -> None:
    path = local_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def save_local_peer(host: str, token: str, name: str = "") -> bool:
    """Remember the token this machine was given by ``host``."""
    key = str(host or "").strip().lower().rstrip("/")
    if not key or not token:
        return False
    with _LOCK:
        data = _read_local()
        data[key] = {"host": key, "token": str(token), "name": str(name or key)[:120], "paired_at": _now()}
        _write_local(data)
    return True


def load_local_peer(host: str) -> dict | None:
    key = str(host or "").strip().lower().rstrip("/")
    entry = _read_local().get(key)
    return entry if isinstance(entry, dict) else None


def forget_local_peer(host: str) -> bool:
    key = str(host or "").strip().lower().rstrip("/")
    with _LOCK:
        data = _read_local()
        if key not in data:
            return False
        data.pop(key)
        _write_local(data)
    return True
