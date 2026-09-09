"""Resolve plugin/skill uninstall ids and hide bundled seeds in this workspace.

Gateway and launcher both use this so uninstall still works when:
- UI sends plugin.json ``name`` (whisper_transcribe) instead of dir (whisper)
- Launcher is an older process that 404s on the json name
"""

from __future__ import annotations

import json
import os
import re

from opensquad._syscfg._workspace import (
    builtin_resources_dir,
    is_locally_uninstalled,
    mark_locally_uninstalled,
    resource_search_dirs,
    workspace_plugins_dir,
)

PLUGIN_ID_ALIASES = {
    "whisper_transcribe": "whisper",
}

_PROTECTED_CACHE: set[str] | None = None


def _plugin_roots() -> list[str]:
    try:
        return list(resource_search_dirs("plugins"))
    except Exception:
        return [builtin_resources_dir("plugins"), workspace_plugins_dir()]


def _iter_plugin_dirs() -> dict[str, str]:
    """dir_name -> path; workspace wins. Does not apply the uninstall filter."""
    out: dict[str, str] = {}
    for root in reversed(_plugin_roots()):
        if not os.path.isdir(root):
            continue
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for entry in entries:
            if entry.startswith(("_", ".")):
                continue
            plugin_dir = os.path.join(root, entry)
            if os.path.isdir(plugin_dir):
                out[entry] = plugin_dir
    return out


def _declared_plugin_name(plugin_dir: str, fallback: str) -> str:
    manifest = os.path.join(plugin_dir, "plugin.json")
    if not os.path.isfile(manifest):
        return fallback
    try:
        with open(manifest, encoding="utf-8") as f:
            meta = json.load(f)
        declared = str(meta.get("name") or "").strip()
        if declared:
            return declared
    except Exception:
        pass
    return fallback


def protected_plugin_ids() -> set[str]:
    global _PROTECTED_CACHE
    if _PROTECTED_CACHE is not None:
        return _PROTECTED_CACHE
    path = os.path.join(builtin_resources_dir("plugins"), "builtin_plugins.json")
    names: set[str] = set()
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f).get("plugins") or {}
            names = {str(k) for k in data}
        except Exception:
            names = set()
    _PROTECTED_CACHE = names
    return names


def resolve_plugin_dir_name(name: str) -> str | None:
    """Map UI uninstall id to on-disk plugin directory name."""
    if not name:
        return None
    alias = PLUGIN_ID_ALIASES.get(name, name)
    dirs = _iter_plugin_dirs()
    if alias in dirs:
        return alias
    if name in dirs:
        return name
    for dir_name, plugin_dir in dirs.items():
        if _declared_plugin_name(plugin_dir, dir_name) == name:
            return dir_name
    if alias != name:
        return alias
    return None


def resolve_skill_dir_name(name: str) -> str | None:
    if not name:
        return None
    try:
        roots = list(resource_search_dirs("skills"))
    except Exception:
        roots = [builtin_resources_dir("skills")]
    found: dict[str, str] = {}
    for root in reversed(roots):
        if not os.path.isdir(root):
            continue
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for entry in entries:
            skill_dir = os.path.join(root, entry)
            if os.path.isdir(skill_dir):
                found[entry] = skill_dir
    if name in found:
        return name
    for dir_name, skill_dir in found.items():
        skill_json = os.path.join(skill_dir, "skill.json")
        if os.path.isfile(skill_json):
            try:
                with open(skill_json, encoding="utf-8") as f:
                    meta = json.load(f)
                if str(meta.get("name") or "").strip() == name:
                    return dir_name
            except Exception:
                pass
    return None


def plugin_tombstone_ids(name: str) -> list[str]:
    """All ids that must be hidden so list/filter cannot resurrect the plugin."""
    if not name:
        return []
    dir_name = resolve_plugin_dir_name(name) or name
    out: list[str] = []
    for item in (dir_name, name, PLUGIN_ID_ALIASES.get(name), PLUGIN_ID_ALIASES.get(dir_name)):
        if item and item not in out:
            out.append(item)
    for json_name, alias in PLUGIN_ID_ALIASES.items():
        if alias in (dir_name, name) and json_name not in out:
            out.append(json_name)
    return out


def hide_resource(kind: str, dir_name: str) -> None:
    mark_locally_uninstalled(kind, dir_name)
    if kind == "plugins":
        for extra in plugin_tombstone_ids(dir_name):
            mark_locally_uninstalled(kind, extra)


def hide_plugin(name: str) -> str:
    """Hide a plugin in this workspace. Returns the on-disk directory name."""
    dir_name = resolve_plugin_dir_name(name) or name
    for item in plugin_tombstone_ids(name):
        mark_locally_uninstalled("plugins", item)
    return dir_name


def prepare_plugin_uninstall(name: str) -> tuple[bool, str]:
    """Shared uninstall prep: sanitize, reject protected plugins, resolve+hide.

    Used by both the admin and market uninstall routes so plugin.json ``name``
    (whisper_transcribe) and the on-disk directory (whisper) stay aligned.

    Returns ``(ok, value)``. When ``ok`` is True, ``value`` is the on-disk
    directory name; otherwise ``value`` is the HTTP 400 error detail.
    """
    if not name or not re.match(r"^[a-zA-Z0-9_\-\.]+$", name):
        return False, "Invalid plugin id"
    if is_protected_plugin(name):
        return False, f"Plugin '{name}' is a system plugin and cannot be uninstalled."
    # Hide before deletion so a missing overlay / bundled seed is not user-facing.
    return True, hide_plugin(name)


def is_protected_plugin(name: str) -> bool:
    if not name:
        return False
    protected = protected_plugin_ids()
    dir_name = resolve_plugin_dir_name(name) or name
    return name in protected or dir_name in protected


def filter_plugin_list(plugins: list[dict]) -> list[dict]:
    out = []
    for plugin in plugins:
        dir_name = str(plugin.get("dir_name") or "")
        json_name = str(plugin.get("name") or "")
        ids = {dir_name, json_name, PLUGIN_ID_ALIASES.get(json_name, "")}
        if any(i and is_locally_uninstalled("plugins", i) for i in ids):
            continue
        out.append(plugin)
    return out


def filter_skill_list(skills: list[dict]) -> list[dict]:
    out = []
    for skill in skills:
        dir_name = str(skill.get("dir") or skill.get("dir_name") or "")
        json_name = str(skill.get("name") or "")
        if (dir_name and is_locally_uninstalled("skills", dir_name)) or (
            json_name and is_locally_uninstalled("skills", json_name)
        ):
            continue
        out.append(skill)
    return out
