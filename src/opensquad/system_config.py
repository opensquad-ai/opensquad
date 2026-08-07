"""
OpenSquad Unified System Configuration Reader

All system-level configs are stored in system_config.json.
Priority: env var > system_config.json > hardcoded default.

This module has two layers:
  1. Module-level functions (for direct import): open-squad, 保持向后兼容
  2. The ``syscfg`` singleton (dot-access): syscfg.port('gateway')

For new code, prefer importing from ``opensquad._syscfg`` directly:
    from opensquad._syscfg import port, get_workspace, set_workspace

This module is maintained for backward compatibility; it delegates to the
``opensquad._syscfg`` sub-package internally.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layer 1: Re-export from _syscfg sub-package (single source of truth)
# ---------------------------------------------------------------------------

from opensquad._syscfg import (
    async_result_ttl,
    auth,
    builtin_resources_dir,
    client_host,
    cors_origins,
    default_agent_id,
    default_timeout,
    external_adapter_url,
    gateway_http,
    gateway_register_url,
    gateway_ws,
    get,
    get_builtin_root,
    get_int,
    get_workspace,
    host,
    launcher_url,
    log_backup_count,
    log_date_format,
    log_dir,
    log_format,
    log_level,
    log_max_size_mb,
    port,
    raw,
    reload,
    resource_search_dirs,
    sensevoice_url,
    set_workspace,
    tool_call_debug,
    tool_call_debug_backup_count,
    tool_call_debug_max_size_mb,
    websearch_url,
    whisper_url,
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

# Import path builders (uses _WORKSPACE_ROOT)
from opensquad._syscfg._paths import (
    workspace_agents_dir as _wad,
)
from opensquad._syscfg._paths import (
    workspace_logs_dir as _wld,
)
from opensquad._syscfg._paths import (
    workspace_metadata_dir as _wmd,
)
from opensquad._syscfg._paths import (
    workspace_sessions_dir as _wsd,
)
from opensquad._syscfg._paths import (
    workspace_uploads_dir as _wud,
)

# ---------------------------------------------------------------------------
# Workspace lifecycle (cross-cutting, defined here for direct use by _syscfg)
# ---------------------------------------------------------------------------
# Import path constants from _workspace
from opensquad._syscfg._workspace import (
    _CONFIG_PATH,
    _DEFAULT_ROOT,
    _WORKSPACE_ROOT,
)

# ---------------------------------------------------------------------------
# ensure_workspace_structure / init_workspace (cross-cutting, live here)
# ---------------------------------------------------------------------------


def ensure_workspace_structure():
    """Ensure the workspace directory structure exists, creating it if absent."""
    dirs_to_create = [
        _wmd(),
        _wud(),
        _wld("gateway"),
        _wsd(),
        workspace_data_dir("ai_his_talk"),
        workspace_data_dir("plugins"),
        workspace_data_dir("audit"),
        _wad(),
        workspace_plugins_dir(),
        workspace_skills_dir(),
        workspace_role_cards_dir(),
        workspace_collab_cards_dir(),
        workspace_model_cards_dir(),
        workspace_gateway_dir("backend", "sessions"),
        workspace_gateway_dir("backend", "tasks"),
        workspace_gateway_dir("backend", "uploads"),
    ]
    for d in dirs_to_create:
        os.makedirs(d, exist_ok=True)
    logger.info("[syscfg] Workspace structure ensured at: %s", _WORKSPACE_ROOT)


def init_workspace(workspace_path: str, copy_config: bool = True):
    """Initialize a new workspace."""
    import shutil
    from datetime import datetime, timezone

    set_workspace(workspace_path)
    ensure_workspace_structure()

    workspace_meta = {
        "version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat() + "Z",
        "opensquad_version": "1.2.3",
        "workspace_name": os.path.basename(workspace_path),
    }
    meta_path = _wmd("workspace.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(workspace_meta, f, indent=2, ensure_ascii=False)
    logger.info("[syscfg] Created workspace metadata: %s", meta_path)

    if copy_config:
        template_path = os.path.join(_DEFAULT_ROOT, "system_config.template.json")
        if not os.path.exists(template_path):
            template_path = os.path.join(_DEFAULT_ROOT, "system_config.json")
        if not os.path.exists(template_path):
            template_path = os.path.join(_DEFAULT_ROOT, "system_config.example.json")

        target_path = os.path.join(workspace_path, "system_config.json")
        if os.path.exists(template_path):
            shutil.copy2(template_path, target_path)
            logger.info("[syscfg] Copied config template to: %s", target_path)


def is_service_enabled(plugin_name: str) -> bool:
    """Check if a plugin service is enabled via services.{plugin_name}.enabled in system_config.json.

    Q-2: single source of truth — delegate to ``_syscfg._config`` so the four
    near-duplicate implementations cannot drift again.
    """
    from opensquad._syscfg._config import is_service_enabled as _impl

    return _impl(plugin_name)


# ---------------------------------------------------------------------------
# Node identity (from _network.py)
# ---------------------------------------------------------------------------
# Node identity (from _config.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Context compression (from _config.py)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# VCS (from _config.py)
# ---------------------------------------------------------------------------
from opensquad._syscfg._config import (
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
    filesystem_workspace_dirs,
    github_plugins_token,
    node_id,
    node_label,
    node_register_to_gateway,
    node_secret,
    project_root,
    vcs_default_branch,
    vcs_default_remote,
    vcs_git_server,
)

# ---------------------------------------------------------------------------
# Layer 2: _SysCfg singleton wrapper (backward compatibility)
# ---------------------------------------------------------------------------


class _SysCfg:
    """Namespace for dot-access: syscfg.port('gateway')"""

    set_workspace = staticmethod(set_workspace)
    get_workspace = staticmethod(get_workspace)
    get_builtin_root = staticmethod(get_builtin_root)
    workspace_data_dir = staticmethod(workspace_data_dir)
    workspace_agents_dir = staticmethod(workspace_agents_dir)
    workspace_gateway_dir = staticmethod(workspace_gateway_dir)
    workspace_db_path = staticmethod(workspace_db_path)
    workspace_sessions_dir = staticmethod(workspace_sessions_dir)
    workspace_logs_dir = staticmethod(workspace_logs_dir)
    workspace_uploads_dir = staticmethod(workspace_uploads_dir)
    workspace_metadata_dir = staticmethod(workspace_metadata_dir)
    workspace_plugins_dir = staticmethod(workspace_plugins_dir)
    workspace_skills_dir = staticmethod(workspace_skills_dir)
    workspace_role_cards_dir = staticmethod(workspace_role_cards_dir)
    workspace_collab_cards_dir = staticmethod(workspace_collab_cards_dir)
    workspace_model_cards_dir = staticmethod(workspace_model_cards_dir)
    resource_search_dirs = staticmethod(resource_search_dirs)
    builtin_resources_dir = staticmethod(builtin_resources_dir)
    workspace_config_path = staticmethod(lambda: _CONFIG_PATH)
    ensure_workspace_structure = staticmethod(ensure_workspace_structure)
    init_workspace = staticmethod(init_workspace)
    is_service_enabled = staticmethod(is_service_enabled)

    port = staticmethod(port)
    host = staticmethod(host)
    client_host = staticmethod(client_host)
    cors_origins = staticmethod(cors_origins)
    auth = staticmethod(auth)
    get = staticmethod(get)
    get_int = staticmethod(get_int)
    gateway_http = staticmethod(gateway_http)
    gateway_ws = staticmethod(gateway_ws)
    gateway_register_url = staticmethod(gateway_register_url)
    launcher_url = staticmethod(launcher_url)
    external_adapter_url = staticmethod(external_adapter_url)
    whisper_url = staticmethod(whisper_url)
    sensevoice_url = staticmethod(sensevoice_url)
    websearch_url = staticmethod(websearch_url)
    default_agent_id = staticmethod(default_agent_id)
    default_timeout = staticmethod(default_timeout)
    async_result_ttl = staticmethod(async_result_ttl)

    log_dir = staticmethod(log_dir)
    log_max_size_mb = staticmethod(log_max_size_mb)
    log_backup_count = staticmethod(log_backup_count)
    log_level = staticmethod(log_level)
    log_format = staticmethod(log_format)
    log_date_format = staticmethod(log_date_format)
    tool_call_debug = staticmethod(tool_call_debug)
    tool_call_debug_max_size_mb = staticmethod(tool_call_debug_max_size_mb)
    tool_call_debug_backup_count = staticmethod(tool_call_debug_backup_count)

    project_root = staticmethod(project_root)
    filesystem_workspace_dirs = staticmethod(filesystem_workspace_dirs)
    raw = staticmethod(raw)
    reload = staticmethod(reload)

    node_id = staticmethod(node_id)
    node_label = staticmethod(node_label)
    node_register_to_gateway = staticmethod(node_register_to_gateway)
    github_plugins_token = staticmethod(github_plugins_token)
    ensure_node_id = staticmethod(ensure_node_id)
    ensure_external_api_key = staticmethod(ensure_external_api_key)
    ensure_gateway_token = staticmethod(ensure_gateway_token)
    ensure_node_secret = staticmethod(ensure_node_secret)
    node_secret = staticmethod(node_secret)

    ctx_trigger_threshold = staticmethod(ctx_trigger_threshold)
    ctx_keep_recent_fraction = staticmethod(ctx_keep_recent_fraction)
    ctx_recent_hard_cap_frac = staticmethod(ctx_recent_hard_cap_frac)
    ctx_keep_recent_rounds = staticmethod(ctx_keep_recent_rounds)
    ctx_summary_max_tokens = staticmethod(ctx_summary_max_tokens)
    ctx_conv_text_budget_chars = staticmethod(ctx_conv_text_budget_chars)

    vcs_git_server = staticmethod(vcs_git_server)
    vcs_default_remote = staticmethod(vcs_default_remote)
    vcs_default_branch = staticmethod(vcs_default_branch)


syscfg = _SysCfg()
