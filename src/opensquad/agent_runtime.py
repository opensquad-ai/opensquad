"""Bundled Agent Python runtime discovery for frozen desktop builds."""

from __future__ import annotations

import json
import os
import sys

MANIFEST_FILENAME = "agent-runtime.json"


def _default_runtime_python() -> str | None:
    """Well-known install location written by the desktop setup wizard."""
    if sys.platform != "win32":
        return None
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    candidate = os.path.join(local, "OpenSquad", "runtime", "python311", "python.exe")
    return candidate if os.path.isfile(candidate) else None


def manifest_path() -> str | None:
    app_data = os.environ.get("OPENSQUAD_APP_DATA")
    if not app_data:
        return None
    return os.path.join(app_data, MANIFEST_FILENAME)


def read_manifest() -> dict | None:
    path = manifest_path()
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def resolve_bundled_agent_python() -> str | None:
    """Return the installer-managed Python for agent/plugin child processes."""
    override = os.environ.get("OPENSQUAD_AGENT_RUNTIME")
    if override:
        override = os.path.abspath(override)
        if os.path.isfile(override):
            return override

    data = read_manifest()
    if data:
        exe = data.get("python")
        if isinstance(exe, str):
            exe = os.path.abspath(exe)
            if os.path.isfile(exe):
                return exe

    default = _default_runtime_python()
    if default:
        return default
    return None
