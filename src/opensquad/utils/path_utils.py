"""
Unified path safety & workspace root utilities.

Consolidates ``_get_workspace_root`` and ``_is_path_safe`` that were
duplicated across ``tools/filesystem.py``, ``tools/system.py`` and
``system.py`` with slight behavioural differences.

Usage::

    from opensquad.utils.path_utils import get_workspace_root, is_path_safe
"""

from __future__ import annotations

import os

# ── Extra allowed directories (injected by agents_boot.py at startup) ──
_EXTRA_ALLOWED_DIRS: list[str] = []


def set_allowed_dirs(dirs: list[str]) -> None:
    """Set the extra allowed working directory whitelist (called at startup)."""
    global _EXTRA_ALLOWED_DIRS
    resolved = []
    for d in dirs:
        if d:
            p = d if os.path.isabs(d) else os.path.join(get_workspace_root(), d)
            resolved.append(os.path.normcase(os.path.abspath(p)))
    _EXTRA_ALLOWED_DIRS = resolved


def get_workspace_root() -> str:
    """Return the current workspace root (fallback to process cwd).

    Resolution order:
    1. ``AgentContext.session_cwd`` — per-session override set via the
       folder-picker UI. When set, all shell commands and file operations
       default to this directory.
    2. ``syscfg.get_workspace()`` — the permanent shared workspace folder.
    3. ``os.getcwd()`` — last resort fallback.
    """
    # 1. Check session_cwd from AgentContext (per-session override)
    try:
        from opensquad._context import get_current_context

        ctx = get_current_context()
        if ctx and ctx.session_cwd and os.path.isdir(ctx.session_cwd):
            return os.path.normcase(os.path.abspath(ctx.session_cwd))
    except Exception:
        pass
    # 2. Fall back to permanent workspace root
    try:
        from opensquad.system_config import syscfg

        ws = syscfg.get_workspace()
        if ws and os.path.isdir(ws):
            return os.path.normcase(os.path.abspath(ws))
    except Exception:
        pass
    # 3. Last resort
    return os.path.normcase(os.path.abspath(os.getcwd()))


def is_path_safe(
    path: str,
    *,
    extra_allowed_dirs: list[str] | None = None,
) -> bool:
    """Check whether *path* is within the workspace root or an allowed directory.

    Parameters
    ----------
    path:
        Absolute or relative path to check.
    extra_allowed_dirs:
        Extra directories to allow (e.g. from agent whitelist).
        Falls back to the globally configured ``_EXTRA_ALLOWED_DIRS``.

    Returns
    -------
    True if the path is under the workspace root or one of the allowed dirs.
    """
    try:
        root = get_workspace_root()
        abs_path = (
            os.path.normcase(os.path.abspath(path))
            if os.path.isabs(path)
            else os.path.normcase(os.path.abspath(os.path.join(root, path)))
        )

        # Primary check: under workspace root
        if os.path.commonpath([root, abs_path]) == root:
            return True

        # Secondary check: under any allowed directory
        allowed = _EXTRA_ALLOWED_DIRS if extra_allowed_dirs is None else extra_allowed_dirs
        for adir in allowed:
            try:
                if os.path.commonpath([adir, abs_path]) == adir:
                    return True
            except Exception:
                continue

        return False
    except Exception:
        return False
