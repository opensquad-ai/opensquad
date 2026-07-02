"""
_syscfg/_workspace.py -- Workspace lifecycle, path constants, and directory builders.

Extracted from system_config.py.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Locate package root
_PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
_MODULE_ROOT = os.path.dirname(_PACKAGE_ROOT)
_DEFAULT_ROOT = os.path.dirname(_MODULE_ROOT)


def _resolve_initial_workspace() -> str:
    """Pick workspace root before ``set_workspace()`` / bootstrap run.

    In a PyInstaller bundle installed under Program Files, ``_DEFAULT_ROOT``
    points at ``_internal/`` which is read-only.  Electron passes writable
    paths via ``OPENSQUAD_USER_DATA`` / ``OPENSQUAD_APP_DATA`` before spawning
    ``run.exe``; honour them here so import-time side effects (e.g.
    ``session_manager`` creating ``data/sessions/``) never touch the install dir.
    """
    for key in ("OPENSQUAD_WORKSPACE", "OPENSQUAD_USER_DATA", "OPENSQUAD_APP_DATA"):
        raw = os.environ.get(key, "").strip()
        if raw:
            return os.path.abspath(raw)
    return _DEFAULT_ROOT


# Workspace root -- mutable via set_workspace()
_WORKSPACE_ROOT = _resolve_initial_workspace()
_CONFIG_PATH = os.path.join(_WORKSPACE_ROOT, "system_config.json")


def get_workspace() -> str:
    """Return the current workspace directory."""
    return _WORKSPACE_ROOT


def get_builtin_root() -> str:
    """Return the builtin project root (where the package is installed)."""
    return _DEFAULT_ROOT


def set_workspace(path: str) -> None:
    """Set the workspace directory (where config and data live)."""
    global _WORKSPACE_ROOT, _CONFIG_PATH
    _WORKSPACE_ROOT = os.path.abspath(path)
    _CONFIG_PATH = os.path.join(_WORKSPACE_ROOT, "system_config.json")
    logger.info("[syscfg.workspace] Workspace set to: %s", _WORKSPACE_ROOT)


def get_config_path() -> str:
    """Return the current system_config.json path."""
    return _CONFIG_PATH


# ========================================================================
# Path builders
# ========================================================================


def workspace_data_dir(*subpaths: str) -> str:
    """Return the workspace data directory path."""
    return os.path.join(_WORKSPACE_ROOT, "data", *subpaths)


def workspace_agents_dir(*subpaths: str) -> str:
    """Return the workspace agents directory path."""
    return os.path.join(_WORKSPACE_ROOT, "agents", *subpaths)


def workspace_gateway_dir(*subpaths: str) -> str:
    """Return the workspace gateway data directory path."""
    return os.path.join(_WORKSPACE_ROOT, "gateway", *subpaths)


def workspace_db_path(db_name: str = "chat.db") -> str:
    """Return the workspace database file path."""
    return workspace_gateway_dir("backend", db_name)


def workspace_sessions_dir(*subpaths: str) -> str:
    """Return the workspace sessions directory path."""
    return workspace_data_dir("sessions", *subpaths)


def workspace_logs_dir(*subpaths: str) -> str:
    """Return the workspace logs directory path."""
    return workspace_data_dir("logs", *subpaths)


def workspace_uploads_dir(*subpaths: str) -> str:
    """Return the workspace uploads directory path."""
    return workspace_data_dir("uploads", *subpaths)


def workspace_plugins_dir(*subpaths: str) -> str:
    """Return the workspace plugins directory path (user installs / uploads)."""
    return os.path.join(_WORKSPACE_ROOT, "plugins", *subpaths)


def workspace_skills_dir(*subpaths: str) -> str:
    """Return the workspace skills directory path (user installs / uploads)."""
    return os.path.join(_WORKSPACE_ROOT, "skills", *subpaths)


def workspace_role_cards_dir(*subpaths: str) -> str:
    """Return the workspace role cards directory path."""
    return os.path.join(_WORKSPACE_ROOT, "role_cards", *subpaths)


def workspace_collab_cards_dir(*subpaths: str) -> str:
    """Return the workspace collab cards directory path."""
    return os.path.join(_WORKSPACE_ROOT, "collab_cards", *subpaths)


def workspace_model_cards_dir(*subpaths: str) -> str:
    """Return the workspace model cards directory path."""
    return os.path.join(_WORKSPACE_ROOT, "model_cards", *subpaths)


def resource_search_dirs(resource_type: str) -> list[str]:
    """Return [workspace, builtin] dirs for reads; workspace entries override builtin."""
    builders = {
        "plugins": workspace_plugins_dir,
        "skills": workspace_skills_dir,
        "role_cards": workspace_role_cards_dir,
        "collab_cards": workspace_collab_cards_dir,
        "model_cards": workspace_model_cards_dir,
    }
    if resource_type not in builders:
        raise ValueError(f"Invalid resource_type: {resource_type}")
    seen: set[str] = set()
    ordered: list[str] = []
    for d in (builders[resource_type](), builtin_resources_dir(resource_type)):
        ab = os.path.abspath(d)
        if ab not in seen:
            seen.add(ab)
            ordered.append(d)
    return ordered


def workspace_metadata_dir(*subpaths: str) -> str:
    """Return the workspace metadata directory path."""
    return os.path.join(_WORKSPACE_ROOT, ".opensquad", *subpaths)


def builtin_resources_dir(resource_type: str, *subpaths: str) -> str:
    """
    Return the installation directory resource path (read-only).

    resource_type: "plugins", "skills", "role_cards", "collab_cards", "model_cards"
    """
    valid_types = {"plugins", "skills", "role_cards", "collab_cards", "model_cards"}
    if resource_type not in valid_types:
        raise ValueError(f"Invalid resource_type: {resource_type}. Must be one of {valid_types}")

    root = os.environ.get("OPENSQUAD_BUILTIN_ROOT")
    if not root:
        from ._config import _load as _cfg_load

        cfg = _cfg_load()
        root = cfg.get("builtin_resources_root", "")
    if root:
        return os.path.join(os.path.abspath(root), resource_type, *subpaths)
    return os.path.join(_DEFAULT_ROOT, resource_type, *subpaths)
