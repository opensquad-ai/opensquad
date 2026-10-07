"""Plugin-contributed boot screen (缝 B) — resolver for the shell's startup animation.

A plugin may declare a startup animation in its manifest::

    {
      "name": "my_boot_screen",
      "enabled": true,
      "contributes": {
        "bootScreen": {"video": "assets/boot.mp4", "poster": "assets/boot.jpg", "holdMs": 3000}
      }
    }

Unlike an "inject a script into index.html" seam (缝 A), the core owns the boot
loader and only asks *what to play*: this module turns the enabled plugin's
declaration into a URL the shell can hand to a ``<video>``. **No plugin code runs
in the shell**, so the trust boundary is unchanged (the mods matrix refuses
``Client`` elements for exactly that reason).

Private vs. shipped
-------------------
Roots are searched in priority order — **workspace plugins first, then the
shipped (builtin) ones**::

    <workspace>/plugins/<name>/   <- your own animation; never packaged
    <builtin>/plugins/<name>/     <- what a release ships

So a private animation lives entirely outside the repo (and outside the wheel /
PyInstaller bundle), while a clean public install falls back to the shipped
plugin — or, when none declares one, to the built-in loader already in
``index.html``.

Assets are served by a single confined route (``/api/ai-web/boot-screen/asset``)
that accepts only ``kind=video|poster`` and serves whatever the resolver picked.
No directory is ever exposed, and the caller cannot point it at a path.

Everything here is pure file reading: no network, no subprocess, no import of a
plugin's code.  Roots are injectable, so tests never touch the real workspace.

Design: ``docs/plugin-boot-screen.md``.
"""

from __future__ import annotations

import json
import os
from typing import Any

# The shell's `<video src>` points here. Query-only: the route re-resolves and
# serves one file, so the client can never name a path.
ASSET_ROUTE = "/api/ai-web/boot-screen/asset"

# A startup animation must never hold the shell hostage for long; the frontend
# also removes the overlay on `ended`, on click, and on any key.
MAX_HOLD_MS = 120_000

_ASSET_KINDS = ("video", "poster")


def _read_json(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _plugin_meta(plugin_dir: str) -> dict[str, Any]:
    return _read_json(os.path.join(plugin_dir, "plugin.json"))


def _safe_rel(value: Any) -> str:
    """A plugin-relative asset path, or ``""`` when it is not one.

    Rejects absolute paths, ``..`` segments, and anything that looks like a URL
    scheme — the file must stay inside its own plugin directory.
    """
    if not isinstance(value, str):
        return ""
    rel = value.strip().replace("\\", "/")
    if not rel or rel.startswith("/"):
        return ""
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return ""
    if ":" in parts[0]:  # drive letter / scheme, e.g. "http:" or "C:"
        return ""
    return rel


def _asset_path(plugin_dir: str, rel: str) -> str:
    return os.path.join(plugin_dir, *rel.split("/"))


def _hold_ms(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    if n <= 0:
        return 0
    return min(n, MAX_HOLD_MS)


def workspace_plugins_root() -> str:
    from opensquad.system_config import syscfg

    return syscfg.workspace_plugins_dir()


def builtin_plugins_root() -> str:
    from opensquad.system_config import syscfg

    return syscfg.builtin_resources_dir("plugins")


def boot_screen_roots() -> list[str]:
    """Search order: the operator's own plugins win over the shipped ones."""
    return [workspace_plugins_root(), builtin_plugins_root()]


def _pick(roots: list[str]) -> dict[str, Any] | None:
    """The first enabled plugin (in root, then name order) with a usable video."""
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            if entry.startswith((".", "_")):
                continue
            plugin_dir = os.path.join(root, entry)
            if not os.path.isdir(plugin_dir):
                continue
            meta = _plugin_meta(plugin_dir)
            if not meta.get("enabled", True):
                continue
            contribution = (meta.get("contributes") or {}).get("bootScreen")
            if not isinstance(contribution, dict):
                continue
            video_rel = _safe_rel(contribution.get("video"))
            if not video_rel or not os.path.isfile(_asset_path(plugin_dir, video_rel)):
                continue
            return {"dir": plugin_dir, "name": entry, "meta": meta, "contribution": contribution}
    return None


def _asset_url(kind: str, path: str) -> str:
    """A cache-busting URL for one asset, so a replaced clip is never served stale."""
    try:
        version = int(os.path.getmtime(path))
    except OSError:
        version = 0
    return f"{ASSET_ROUTE}?kind={kind}&v={version}"


def resolve_boot_screen(roots: list[str] | None = None) -> dict[str, Any]:
    """What the shell should play, or ``{"enabled": False}`` for the default loader."""
    picked = _pick(roots if roots is not None else boot_screen_roots())
    if picked is None:
        return {"enabled": False}

    contribution = picked["contribution"]
    video_path = _asset_path(picked["dir"], _safe_rel(contribution.get("video")))

    poster = ""
    poster_rel = _safe_rel(contribution.get("poster"))
    if poster_rel:
        poster_path = _asset_path(picked["dir"], poster_rel)
        if os.path.isfile(poster_path):
            poster = _asset_url("poster", poster_path)

    return {
        "enabled": True,
        "source": str(picked["meta"].get("name") or picked["name"]),
        "plugin": picked["name"],
        "video": _asset_url("video", video_path),
        "poster": poster,
        "holdMs": _hold_ms(contribution.get("holdMs")),
    }


def resolve_asset(kind: str, roots: list[str] | None = None) -> str:
    """Absolute path of an asset the config just advertised, or ``""``.

    ``kind`` is the only thing that comes from the caller — and it is an enum,
    never a path. The file served is whatever the resolver itself picked.
    """
    if kind not in _ASSET_KINDS:
        return ""
    picked = _pick(roots if roots is not None else boot_screen_roots())
    if picked is None:
        return ""
    rel = _safe_rel(picked["contribution"].get(kind))
    if not rel:
        return ""
    path = _asset_path(picked["dir"], rel)
    return path if os.path.isfile(path) else ""
