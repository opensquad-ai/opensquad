"""
Auth helpers for the Launcher Management API handler.

Extracted from the inline ``ManagementHandler`` closure to reduce the
size of ``_launcher_api/__init__.py``.  Each function takes a ``handler``
argument (the ``ManagementHandler`` instance) so it can delegate back
to the base handler for response/syscfg lookups.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

_log = logging.getLogger("launcher_api")


def get_launcher_token() -> str:
    """Read the launcher token from system_config (or env override)."""
    from opensquad.system_config import syscfg

    try:
        return syscfg.get("launcher_token", "")
    except (OSError, ValueError):
        return ""


def _bcrypt_bytes(password: str) -> bytes:
    """Normalize a password to bcrypt-safe bytes (max 72 bytes)."""
    return (password or "").encode("utf-8")[:72]


def encrypt_password(password: str) -> str:
    """Return a bcrypt hash of *password*.

    SEC-4: uses native ``bcrypt`` (passlib 1.7.4 is incompatible with
    bcrypt 5.x) — the same implementation as Gateway's auth.py.
    """
    import bcrypt

    return bcrypt.hashpw(_bcrypt_bytes(password), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, stored: str) -> bool:
    """Verify *password* against a stored hash (bcrypt or legacy SHA-256).

    Supports both bcrypt (starts with ``$2b$``) and legacy salted SHA-256
    (``salt$hash`` format) for backward compatibility.  On successful SHA-256
    verification the caller may wish to re-hash with bcrypt.
    """
    # bcrypt hash — standard path
    if stored.startswith("$2"):
        import bcrypt

        try:
            return bcrypt.checkpw(_bcrypt_bytes(password), stored.encode("utf-8"))
        except Exception:
            return False

    # Legacy plain-text comparison (no salt separator)
    if "$" not in stored:
        _log.warning("Legacy plain-text password comparison detected — stored hash should be updated")
        import hmac as _hmac

        return _hmac.compare_digest(password or "", stored or "")

    # Legacy salted SHA-256 (migration path)
    salt, hashed = stored.split("$", 1)
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest() == hashed


def check_auth(handler: BaseHTTPRequestHandler) -> bool:
    """Check the Authorization header against the configured launcher token.

    SEC-12: if no token is configured, requests are REJECTED (fail closed).
    A launcher API that accepts everything silently is an authentication
    bypass; deployments must configure ``launcher_token``.

    Returns True if authorised, otherwise writes a 401/403 response and
    returns False.
    """
    token = get_launcher_token()
    if not token:
        _log.error(
            "[launcher_auth] launcher_token is NOT configured — requests are REJECTED. "
            "Set launcher_token in system_config.json!"
        )
        _send_json(handler, {"error": "Unauthorized", "message": "launcher_token not configured"}, 401)
        return False
    auth_header = handler.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        _send_json(handler, {"error": "Unauthorized", "message": "Bearer token required"}, 401)
        return False
    provided = auth_header[7:]
    import hmac as _hmac

    if not _hmac.compare_digest(provided, token):
        _send_json(handler, {"error": "Forbidden", "message": "Invalid token"}, 403)
        return False
    return True


def _send_json(handler, data: dict, status: int = 200):
    """Write a JSON response.  Inline copy of the original helper."""
    import json

    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()
        handler.wfile.write(body)
    except (ConnectionError, BrokenPipeError, OSError):
        pass
