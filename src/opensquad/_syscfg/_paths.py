"""
_syscfg/_paths.py -- Re-exports path builders from _workspace.

Kept for backward compatibility reference. New code should import from
opensquad._syscfg directly or from opensquad._syscfg._workspace.
"""

from __future__ import annotations

from ._workspace import (
    builtin_resources_dir,
    resource_search_dirs,
    workspace_agents_dir,
    workspace_collab_cards_dir,
    workspace_data_dir,
    workspace_db_path,
    workspace_gateway_dir,
    workspace_logs_dir,
    workspace_metadata_dir,
    workspace_model_cards_dir,
    workspace_plugins_dir,
    workspace_role_cards_dir,
    workspace_sessions_dir,
    workspace_skills_dir,
    workspace_uploads_dir,
)

__all__ = [
    "builtin_resources_dir",
    "resource_search_dirs",
    "workspace_agents_dir",
    "workspace_collab_cards_dir",
    "workspace_data_dir",
    "workspace_db_path",
    "workspace_gateway_dir",
    "workspace_logs_dir",
    "workspace_metadata_dir",
    "workspace_model_cards_dir",
    "workspace_plugins_dir",
    "workspace_role_cards_dir",
    "workspace_sessions_dir",
    "workspace_skills_dir",
    "workspace_uploads_dir",
]
