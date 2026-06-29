# -*- coding: utf-8 -*-
"""
_syscfg -- Modular system configuration sub-package.

Structure:
    __init__.py    -- Re-exports all public functions
    _workspace.py  -- Workspace lifecycle (set_workspace, get_workspace, etc.)
    _paths.py      -- Workspace-aware directory builders
    _config.py     -- Config file loading and raw access + context/VCS/node identity
    _network.py    -- Network config (ports, hosts, URLs, auth)
    _logging.py    -- Logging configuration
"""
from __future__ import annotations

# Workspace lifecycle
from ._workspace import (
    get_workspace,
    get_builtin_root,
    set_workspace,
    get_config_path,
)

# Path builders (from _workspace)
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

# Config loading + all domain functions that read from config
from ._config import (
    _load,
    reload,
    raw,
    get,
    get_int,
    ensure_workspace_structure,
    init_workspace,
    is_service_enabled,
    ctx_trigger_threshold,
    ctx_keep_recent_fraction,
    ctx_recent_hard_cap_frac,
    ctx_keep_recent_rounds,
    ctx_summary_max_tokens,
    ctx_conv_text_budget_chars,
    vcs_git_server,
    vcs_default_remote,
    vcs_default_branch,
    node_id,
    node_label,
    node_register_to_gateway,
    node_secret,
    github_plugins_token,
    ensure_node_id,
    ensure_external_api_key,
    ensure_gateway_token,
    ensure_node_secret,
    project_root,
    skills_dir,
    filesystem_workspace_dirs,
)

# Network
from ._network import (
    port,
    host,
    client_host,
    cors_origins,
    auth,
    gateway_http,
    gateway_ws,
    gateway_register_url,
    launcher_url,
    external_adapter_url,
    default_agent_id,
    default_timeout,
    async_result_ttl,
    whisper_url,
    websearch_url,
)

# Logging
from ._logging import (
    log_dir,
    log_max_size_mb,
    log_backup_count,
    log_level,
    log_format,
    log_date_format,
    tool_call_debug,
    tool_call_debug_max_size_mb,
    tool_call_debug_backup_count,
)

# Typed config models (Pydantic v2)
from ._models import SystemConfig as SystemConfig

__all__ = [
    # Workspace
    "get_workspace",
    "get_builtin_root",
    "set_workspace",
    "get_config_path",
    "ensure_workspace_structure",
    "init_workspace",
    "is_service_enabled",
    # Paths
    "workspace_data_dir",
    "workspace_agents_dir",
    "workspace_gateway_dir",
    "workspace_db_path",
    "workspace_sessions_dir",
    "workspace_logs_dir",
    "workspace_uploads_dir",
    "workspace_metadata_dir",
    "builtin_resources_dir",
    # Config
    "_load",
    "reload",
    "raw",
    "get",
    "get_int",
    # Network
    "port",
    "host",
    "cors_origins",
    "auth",
    "gateway_http",
    "gateway_ws",
    "gateway_register_url",
    "launcher_url",
    "external_adapter_url",
    "default_agent_id",
    "default_timeout",
    "async_result_ttl",
    "whisper_url",
    "websearch_url",
    # Logging
    "log_dir",
    "log_max_size_mb",
    "log_backup_count",
    "log_level",
    "log_format",
    "log_date_format",
    "tool_call_debug",
    "tool_call_debug_max_size_mb",
    "tool_call_debug_backup_count",
    # Context compression
    "ctx_trigger_threshold",
    "ctx_keep_recent_fraction",
    "ctx_recent_hard_cap_frac",
    "ctx_keep_recent_rounds",
    "ctx_summary_max_tokens",
    "ctx_conv_text_budget_chars",
    # VCS
    "vcs_git_server",
    "vcs_default_remote",
    "vcs_default_branch",
    # Node identity
    "node_id",
    "node_label",
    "node_register_to_gateway",
    "node_secret",
    "github_plugins_token",
    "ensure_node_id",
    "ensure_external_api_key",
    "ensure_gateway_token",
    "ensure_node_secret",
    # Project
    "project_root",
    "skills_dir",
    "filesystem_workspace_dirs",
]
