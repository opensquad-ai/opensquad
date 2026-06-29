# -*- coding: utf-8 -*-
"""
_syscfg/_paths.py -- Re-exports path builders from _workspace.

Kept for backward compatibility reference. New code should import from
opensquad._syscfg directly or from opensquad._syscfg._workspace.
"""
from __future__ import annotations

from ._workspace import (
    workspace_data_dir,
    workspace_agents_dir,
    workspace_gateway_dir,
    workspace_db_path,
    workspace_sessions_dir,
    workspace_logs_dir,
    workspace_uploads_dir,
    workspace_metadata_dir,
    builtin_resources_dir,
)
