"""Node runtime resolution and host process spawn.

Everything here is synchronous on purpose: ``tests/test_async_no_blocking_calls.py``
forbids ``subprocess.Popen`` inside ``async def`` bodies. Async callers hop a
thread via ``asyncio.to_thread``.

Deliberately **no** ``shell=True`` — see docs/mods-bridge-m0.md §1.2. Shell mode
was the original plan (launcher ``service.cmd``) and it re-introduces three
failure modes we measured: cmd quoting of the script path, PATH entries
containing ``_internal`` being stripped by the launcher's
``_sanitize_path_for_child``, and GBK-mojibake diagnostics. An argv list with an
absolute interpreter path has none of them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_HOST_SCRIPT = os.path.join(_PLUGIN_DIR, "host", "host.mjs")
_WIN = sys.platform == "win32"


def host_script_path() -> str:
    return _HOST_SCRIPT


def _explicit_candidates() -> list[str]:
    exe = "node.exe" if _WIN else "node"
    out: list[str] = []
    explicit = (os.environ.get("OPENSQUAD_NODE") or "").strip()
    if explicit:
        out.append(explicit)
    home = (os.environ.get("OPENSQUAD_NODE_HOME") or "").strip()
    if home:
        out.append(os.path.join(home, exe))
    return out


def resolve_node_executable() -> str:
    """Absolute path to a node executable, or ``""`` when unavailable.

    Order: ``OPENSQUAD_NODE`` → ``OPENSQUAD_NODE_HOME``/node → PATH.

    A node shipped *inside* the PyInstaller bundle must be reached through the
    env vars, never through PATH: the launcher sanitises PATH for every child
    (``launcher/process_manager.py:_sanitize_path_for_child`` strips any entry
    containing ``_internal`` / ``backend-win\\run``), so a bundled node is
    invisible to a PATH lookup while still being on disk.
    """
    for cand in _explicit_candidates():
        if os.path.isfile(cand):
            return cand
    return shutil.which("node") or ""


def build_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def spawn_host(
    node_exe: str,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.Popen:
    """Start ``host.mjs`` and return the Popen handle (unreaped)."""
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if _WIN else 0
    return subprocess.Popen(
        [node_exe, _HOST_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        cwd=cwd,
        env=env if env is not None else build_env(),
        creationflags=creationflags,
    )
