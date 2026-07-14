"""Redact secrets from dict/list structures before display."""

from __future__ import annotations

from typing import Any

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "api-key",
        "secret",
        "token",
        "password",
        "access_token",
        "refresh_token",
        "authorization",
        "auth_token",
    }
)


def mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "***"
    return key[:4] + "…" + key[-4:]


def redact_secrets(obj: Any) -> Any:
    """Deep-copy-ish redact of secret fields for JSON display."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in _SECRET_KEYS or lk.endswith("_api_key") or lk.endswith("_secret"):
                out[k] = mask_key(str(v or ""))
            else:
                out[k] = redact_secrets(v)
        return out
    if isinstance(obj, list):
        return [redact_secrets(x) for x in obj]
    return obj
