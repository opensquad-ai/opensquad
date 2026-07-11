"""Local agent avatar helpers (no Dicebear CDN).

Generates a stable robot-face SVG data-URI from a seed string, and normalizes
chat profile field names between disk (`name`/`avatar`) and the web UI
(`chat_user_name`/`chat_user_avatar`).
"""

from __future__ import annotations

import urllib.parse
from typing import Any


def _seed_hue(seed: str) -> int:
    h = 0
    for ch in seed or "default":
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h % 360


def local_bot_avatar_data_uri(seed: str = "default") -> str:
    """Deterministic robot-face avatar as an SVG data-URI."""
    hue = _seed_hue(seed)
    bg = f"hsl({hue} 42% 48%)"
    accent = f"hsl({(hue + 40) % 360} 55% 70%)"
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">'
        f'<rect width="128" height="128" rx="24" fill="{bg}"/>'
        f'<rect x="28" y="36" width="72" height="64" rx="14" fill="#fff" fill-opacity="0.92"/>'
        f'<circle cx="50" cy="62" r="8" fill="{bg}"/>'
        f'<circle cx="78" cy="62" r="8" fill="{bg}"/>'
        f'<rect x="48" y="80" width="32" height="6" rx="3" fill="{accent}"/>'
        f'<rect x="58" y="22" width="12" height="16" rx="4" fill="#fff" fill-opacity="0.85"/>'
        f'<circle cx="64" cy="18" r="6" fill="{accent}"/>'
        f"</svg>"
    )
    return "data:image/svg+xml;charset=utf-8," + urllib.parse.quote(svg)


def is_external_dicebear(url: str | None) -> bool:
    if not url:
        return False
    lower = url.lower()
    return "dicebear.com" in lower


def ensure_agent_avatar(avatar: str | None, seed: str) -> str:
    """Return a usable avatar URL; replace empty / Dicebear with local bot SVG."""
    if not avatar or is_external_dicebear(avatar):
        return local_bot_avatar_data_uri(seed)
    return avatar


def normalize_chat_profile(raw: dict[str, Any] | None, *, user_id: str = "") -> dict[str, Any]:
    """Normalize profile.json keys for both backend and frontend consumers."""
    if not raw:
        raw = {}
    name = raw.get("chat_user_name") or raw.get("name") or ""
    avatar = raw.get("chat_user_avatar") if raw.get("chat_user_avatar") is not None else raw.get("avatar")
    avatar = avatar or ""
    uid = raw.get("chat_user_id") or user_id or ""
    return {
        "name": name,
        "avatar": avatar,
        "chat_user_name": name,
        "chat_user_avatar": avatar or None,
        "chat_user_id": uid,
    }


def read_agent_profile_file(agents_dir: str, agent_name: str) -> dict[str, Any]:
    """Read profile.json from canonical or legacy path."""
    import json
    import os

    candidates = [
        os.path.join(agents_dir, agent_name, "data", "profile.json"),
        os.path.join(agents_dir, agent_name, "data", "group_chat", "profile.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return normalize_chat_profile(data)
            except (OSError, ValueError, TypeError):
                continue
    return normalize_chat_profile({})
