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

# Config loading + all domain functions that read from config
from ._config import (
    _load,
    ctx_conv_text_budget_chars,
    ctx_keep_recent_fraction,
    ctx_keep_recent_rounds,
    ctx_recent_hard_cap_frac,
    ctx_summary_max_tokens,
    ctx_trigger_threshold,
    ensure_external_api_key,
    ensure_gateway_token,
    ensure_node_id,
    ensure_node_secret,
    ensure_workspace_structure,
    filesystem_workspace_dirs,
    get,
    get_int,
    github_plugins_token,
    init_workspace,
    is_service_enabled,
    node_id,
    node_label,
    node_register_to_gateway,
    node_secret,
    project_root,
    raw,
    reload,
    skills_dir,
    vcs_default_branch,
    vcs_default_remote,
    vcs_git_server,
)

# Logging
from ._logging import (
    log_backup_count,
    log_date_format,
    log_dir,
    log_format,
    log_level,
    log_max_size_mb,
    tool_call_debug,
    tool_call_debug_backup_count,
    tool_call_debug_max_size_mb,
)

# Typed config models (Pydantic v2)
from ._models import SystemConfig as SystemConfig

# Network
from ._network import (
    async_result_ttl,
    auth,
    client_host,
    cors_origins,
    default_agent_id,
    default_timeout,
    external_adapter_url,
    gateway_http,
    gateway_register_url,
    gateway_ws,
    host,
    launcher_url,
    port,
    websearch_url,
    whisper_url,
)

# Workspace lifecycle
# Path builders (from _workspace)
from ._workspace import (
    builtin_resources_dir,
    get_builtin_root,
    get_config_path,
    get_workspace,
    set_workspace,
    workspace_agents_dir,
    workspace_data_dir,
    workspace_db_path,
    workspace_gateway_dir,
    workspace_logs_dir,
    workspace_metadata_dir,
    workspace_sessions_dir,
    workspace_uploads_dir,
)

__all__ = [
    # Config
    "_load",
    "async_result_ttl",
    "auth",
    "builtin_resources_dir",
    "client_host",
    "cors_origins",
    "ctx_conv_text_budget_chars",
    "ctx_keep_recent_fraction",
    "ctx_keep_recent_rounds",
    "ctx_recent_hard_cap_frac",
    "ctx_summary_max_tokens",
    # Context compression
    "ctx_trigger_threshold",
    "default_agent_id",
    "default_timeout",
    "ensure_external_api_key",
    "ensure_gateway_token",
    "ensure_node_id",
    "ensure_node_secret",
    "ensure_workspace_structure",
    "external_adapter_url",
    "filesystem_workspace_dirs",
    "gateway_http",
    "gateway_register_url",
    "gateway_ws",
    "get",
    "get_builtin_root",
    "get_config_path",
    "get_int",
    # Workspace
    "get_workspace",
    "github_plugins_token",
    "host",
    "init_workspace",
    "is_service_enabled",
    "launcher_url",
    "log_backup_count",
    "log_date_format",
    # Logging
    "log_dir",
    "log_format",
    "log_level",
    "log_max_size_mb",
    # Node identity
    "node_id",
    "node_label",
    "node_register_to_gateway",
    "node_secret",
    # Network
    "port",
    # Project
    "project_root",
    "raw",
    "reload",
    "set_workspace",
    "skills_dir",
    "tool_call_debug",
    "tool_call_debug_backup_count",
    "tool_call_debug_max_size_mb",
    "vcs_default_branch",
    "vcs_default_remote",
    # VCS
    "vcs_git_server",
    "websearch_url",
    "whisper_url",
    "workspace_agents_dir",
    # Paths
    "workspace_data_dir",
    "workspace_db_path",
    "workspace_gateway_dir",
    "workspace_logs_dir",
    "workspace_metadata_dir",
    "workspace_sessions_dir",
    "workspace_uploads_dir",
]
