"""Bundled Agent Python runtime discovery for frozen desktop builds."""

from __future__ import annotations

import json
import os
import sys

MANIFEST_FILENAME = "agent-runtime.json"


def _default_runtime_python() -> str | None:
    """Well-known install location written by the desktop setup wizard.

    The wizard may install Python in two layouts:
      - embed mode (legacy): <runtime>/python311/python.exe
      - venv mode (new):     <runtime>/python311/Scripts/python.exe
    Check both. The manifest (read_manifest) is the primary source of
    truth; this function is the last-resort fallback when the manifest
    is missing or corrupt.
    """
    if sys.platform != "win32":
        return None
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    runtime_dir = os.path.join(local, "OpenSquad", "runtime", "python311")
    # venv mode (newer installs): python.exe lives under Scripts/
    venv_exe = os.path.join(runtime_dir, "Scripts", "python.exe")
    if os.path.isfile(venv_exe):
        return venv_exe
    # embed mode (legacy installs): python.exe at the runtime root
    embed_exe = os.path.join(runtime_dir, "python.exe")
    return embed_exe if os.path.isfile(embed_exe) else None


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


def ensure_embed_pth_configured(python_exe: str | None = None) -> bool:
    """Ensure the Agent Python embed's ``._pth`` file has ``import site`` and
    ``Lib\\site-packages`` entries.

    Older setup wizards only added ``import site`` — without an explicit
    ``Lib\\site-packages`` line, some embed builds don't put site-packages on
    ``sys.path`` even with ``import site``, so ``pip install`` succeeds but
    the installed packages are not importable (silent failure → services crash
    with ``ModuleNotFoundError``).

    This is called at launcher startup to fix existing installations created
    by older setup wizards. Returns True if the _pth was modified.
    """
    if sys.platform != "win32":
        return False
    exe = python_exe or resolve_bundled_agent_python()
    if not exe:
        return False
    install_dir = os.path.dirname(exe)
    try:
        pth_name = next(
            (f for f in os.listdir(install_dir) if f.endswith("._pth")),
            None,
        )
    except Exception:
        return False
    if not pth_name:
        return False
    pth_path = os.path.join(install_dir, pth_name)
    try:
        content = open(pth_path, encoding="utf-8").read()
    except Exception:
        return False
    changed = False
    if "import site" not in content:
        content = content.rstrip("\n") + "\nimport site\n"
        changed = True
    if "Lib\\site-packages" not in content:
        content = content.rstrip("\n") + "\nLib\\site-packages\n"
        changed = True
    if changed:
        try:
            with open(pth_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            return False
    return changed
