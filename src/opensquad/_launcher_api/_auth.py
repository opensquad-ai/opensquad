# -*- coding: utf-8 -*-
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
import secrets
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


def encrypt_password(password: str) -> str:
    """Return a bcrypt hash of *password*.

    Uses passlib.hash.bcrypt (same algorithm as Gateway's auth.py).
    """
    try:
        from passlib.hash import bcrypt as passlib_bcrypt
        return passlib_bcrypt.using(rounds=12).hash(password)
    except ImportError:
        _log.warning("passlib not available, falling back to SHA-256 for password hashing")
        salt = secrets.token_hex(16)
        hashed = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
        return f"{salt}${hashed}"


def verify_password(password: str, stored: str) -> bool:
    """Verify *password* against a stored hash (bcrypt or legacy SHA-256).

    Supports both bcrypt (starts with ``$2b$``) and legacy salted SHA-256
    (``salt$hash`` format) for backward compatibility.  On successful SHA-256
    verification the caller may wish to re-hash with bcrypt.
    """
    # bcrypt hash — standard path
    if stored.startswith("$2"):
        try:
            from passlib.hash import bcrypt as passlib_bcrypt
            return passlib_bcrypt.verify(password, stored)
        except ImportError:
            _log.error("passlib not available, cannot verify bcrypt hash")
            return False
        except Exception:
            return False

    # Legacy plain-text comparison (no salt separator)
    if "$" not in stored:
        _log.warning("Legacy plain-text password comparison detected — stored hash should be updated")
        return password == stored

    # Legacy salted SHA-256 (migration path)
    salt, hashed = stored.split("$", 1)
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest() == hashed


def check_auth(handler: BaseHTTPRequestHandler) -> bool:
    """Check the Authorization header against the configured launcher token.

    If no token is configured, ALL requests are accepted (local dev mode).
    This matches the pattern used by node_secret elsewhere in the codebase.

    Returns True if authorised, otherwise writes a 401/403 response and
    returns False.
    """
    token = get_launcher_token()
    if not token:
        # No token configured → allow all requests (local dev mode)
        return True
    auth_header = handler.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        _send_json(handler, {"error": "Unauthorized", "message": "Bearer token required"}, 401)
        return False
    provided = auth_header[7:]
    if provided != token:
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
