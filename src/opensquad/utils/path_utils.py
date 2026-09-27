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

# Module-level session cwd — legacy fallback for sid-less callers (CLI, scheduled
# tasks, serial turns). Per-session values live in ``_SESSION_CWD_BY_SID``.
#
# NOTE: this used to be the *only* session cwd, which made it genuinely
# process-wide and therefore a cross-session coupling: any pane that changed it
# re-rooted every other pane's relative paths, and a write that had been legal
# a moment earlier came back ``403 Path outside project root``. A per-turn
# ContextVar lookup (see ``_current_turn_sid``) now takes precedence, and this
# global survives only for callers that have no session at all.
_SESSION_CWD: str = ""

# ── Per-session working directories (authoritative for project work) ──
# sid -> normcase(abspath(user project folder)). Written by
# ``filesystem.set_session_cwd(path, session_id=…)``.
_SESSION_CWD_BY_SID: dict[str, str] = {}

# Lazily-resolved ``() -> str`` returning the sid of the executing turn.
_turn_sid_provider = None


def _current_turn_sid() -> str:
    """Return the sid of the asyncio task / executor thread now executing.

    The single source of truth is ``session_parallel.get_turn_local()`` — a
    ContextVar set once per turn in ``runner._parallel_session_turn``. It is
    deliberately *not* duplicated here: a second registry of "which session am
    I" is exactly how the two would drift apart.

    This is visible inside executor threads because ``asyncio.to_thread``
    copies the calling context into the worker thread. The old comment on
    ``_SESSION_CWD`` claimed ContextVar was invisible there and used a module
    global instead; that reasoning was wrong and is what coupled all sessions
    to one cwd.

    Imported lazily: ``opensquad.session_parallel`` pulls in ``ingress_policy``
    only (no ``opensquad`` module imports it), so there is no cycle — but a
    module-level import here would still execute before boot wiring.
    """
    global _turn_sid_provider
    if _turn_sid_provider is None:
        try:
            from opensquad.session_parallel import get_turn_local

            def _provider() -> str:
                tl = get_turn_local()
                return str(getattr(tl, "sid", "") or "") if tl is not None else ""

            _turn_sid_provider = _provider
        except Exception:  # pragma: no cover - import guard
            _turn_sid_provider = lambda: ""  # noqa: E731
    try:
        return _turn_sid_provider() or ""
    except Exception:
        return ""


def current_session_id() -> str:
    """Public alias for the sid of the executing turn (``""`` if none)."""
    return _current_turn_sid()


def set_session_cwd_override(path: str | None) -> None:
    """Set/clear the process-wide session working directory override.

    Legacy, sid-less entry point. Retained for callers that genuinely have no
    session (CLI, scheduled tasks, serial turns) and for the existing tests
    that drive it directly. Session-scoped callers must use
    :func:`set_session_cwd_for` instead — writing here makes the value visible
    to *every* session in the process.
    """
    global _SESSION_CWD
    if not path or not str(path).strip():
        _SESSION_CWD = ""
        return
    abs_path = os.path.normcase(os.path.abspath(str(path).strip()))
    _SESSION_CWD = abs_path if os.path.isdir(abs_path) else ""


def get_session_cwd_override() -> str:
    """Return the module-level session cwd override (may be empty)."""
    return _SESSION_CWD


def set_session_cwd_for(session_id: str, path: str | None) -> None:
    """Set (or clear) the working directory of one session.

    ``session_id`` empty degrades to the legacy module-level override so the
    sid-less code paths keep their current behaviour byte for byte.
    """
    sid = (session_id or "").strip()
    if not sid:
        set_session_cwd_override(path)
        return
    if not path or not str(path).strip():
        _SESSION_CWD_BY_SID.pop(sid, None)
        return
    abs_path = os.path.normcase(os.path.abspath(str(path).strip()))
    if os.path.isdir(abs_path):
        _SESSION_CWD_BY_SID[sid] = abs_path


def get_session_cwd_for(session_id: str) -> str:
    """Return one session's working directory, or ``""`` when unset."""
    return _SESSION_CWD_BY_SID.get((session_id or "").strip(), "")


def clear_session_cwd_for(session_id: str) -> None:
    """Forget one session's working directory (drops the tab, no side effects)."""
    _SESSION_CWD_BY_SID.pop((session_id or "").strip(), None)


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
    """Return the effective working directory for tools (fallback to process cwd).

    Resolution order:
    0. The **executing session's** project folder (``_SESSION_CWD_BY_SID``),
       looked up by the sid of the current turn. Two panes working in two
       projects therefore resolve two different roots in the same process.
    1. ``AgentContext.session_cwd`` / module override — legacy, for callers
       with no session (CLI, scheduled tasks, serial turns).
    2. ``syscfg.get_workspace()`` — OpenSquad data/runtime root (agents/, data/, …).
       This is storage, not the user project; used only when no session project is set.
    3. ``os.getcwd()`` — last resort fallback.
    """
    # 0. Current session's folder — per-sid, never shared with a sibling pane.
    sid = _current_turn_sid()
    if sid:
        per_session = _SESSION_CWD_BY_SID.get(sid)
        if per_session and os.path.isdir(per_session):
            return per_session
    # 1. Module-level override (legacy: set by filesystem.set_session_cwd when
    #    no session is known, and by scheduled-task hooks)
    if _SESSION_CWD and os.path.isdir(_SESSION_CWD):
        return _SESSION_CWD
    # 2. Check session_cwd from AgentContext (legacy per-agent override)
    try:
        from opensquad._context import get_current_context

        ctx = get_current_context()
        if ctx and ctx.session_cwd and os.path.isdir(ctx.session_cwd):
            return os.path.normcase(os.path.abspath(ctx.session_cwd))
    except Exception:
        pass
    # 3. Fall back to permanent workspace root
    try:
        from opensquad.system_config import syscfg

        ws = syscfg.get_workspace()
        if ws and os.path.isdir(ws):
            return os.path.normcase(os.path.abspath(ws))
    except Exception:
        pass
    # 4. Last resort
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
