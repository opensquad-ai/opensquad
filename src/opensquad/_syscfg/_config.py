# -*- coding: utf-8 -*-
"""
_syscfg/_config.py -- Config file loading, caching, and raw access.

Extracted from system_config.py.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from . import _workspace as _ws

logger = logging.getLogger(__name__)

# In-memory cache (module-level mutable state)
_cache: Optional[dict] = None
_cache_mtime: float = 0.0
_cache_path_at_load: str = ""


def _config_path() -> str:
    """Live reference to the current config path (reflects workspace changes)."""
    return _ws._CONFIG_PATH


def _load() -> dict:
    """
    Load & cache system_config.json with auto-reload on file change.

    If system_config.json does not exist, falls back to
    system_config.example.json so that fresh clones work out of the box.
    """
    global _cache, _cache_mtime, _cache_path_at_load

    current_mtime = 0.0
    cfg_path = _config_path()

    try:
        if os.path.exists(cfg_path):
            current_mtime = os.path.getmtime(cfg_path)
    except Exception:
        pass

    if (
        _cache is not None
        and _cache_path_at_load == cfg_path
        and current_mtime == _cache_mtime
    ):
        return _cache

    if not os.path.exists(cfg_path):
        last_ws_file = os.path.join(
            os.path.expanduser("~"), ".opensquad", "last_workspace.json"
        )
        if os.path.exists(last_ws_file):
            try:
                with open(last_ws_file, "r", encoding="utf-8") as f:
                    ws_data = json.load(f)
                alt_path = ws_data.get("config_path", "")
                if not (alt_path and os.path.exists(alt_path)):
                    # Fallback: align with workspace_utils.save_last_workspace(),
                    # which writes the "last_workspace" field (a workspace
                    # directory), never "config_path". Independent adapters
                    # (scripts/start_*.bat -> `python -m plugins.<x>.adapter`)
                    # skip the gateway's set_workspace() injection (see
                    # main.py:45-47), so they rely on this branch to locate
                    # the workspace config. Without it they silently fall back
                    # to system_config.example.json placeholders. See issue #44.
                    ws_dir = ws_data.get("last_workspace", "")
                    if ws_dir:
                        ws_cfg = os.path.join(ws_dir, "system_config.json")
                        if os.path.exists(ws_cfg):
                            alt_path = ws_cfg
                if alt_path and os.path.exists(alt_path):
                    _ws._CONFIG_PATH = alt_path
                    cfg_path = alt_path
                    current_mtime = os.path.getmtime(alt_path)
            except Exception:
                pass

    # Try workspace path
    config_paths = [cfg_path]
    ws_name = os.path.basename(os.path.dirname(cfg_path))
    for suffix in [
        f"system_config.{ws_name}.json",
        "system_config.example.json",
        "system_config.gateway.example.json",
    ]:
        candidate = os.path.join(os.path.dirname(cfg_path), suffix)
        if candidate not in config_paths:
            config_paths.append(candidate)

    for candidate_path in config_paths:
        if os.path.exists(candidate_path):
            try:
                with open(candidate_path, "r", encoding="utf-8-sig") as f:
                    _cache = json.load(f)
                _cache_path_at_load = cfg_path
                _cache_mtime = current_mtime
                logger.info("[syscfg] Loaded config from: %s", candidate_path)
                return _cache or {}
            except Exception as e:
                logger.warning("[syscfg] Failed to load config from %s: %s", candidate_path, e)

    logger.warning("[syscfg] No config file found, using empty defaults")
    _cache = {}
    _cache_path_at_load = cfg_path
    _cache_mtime = current_mtime
    return _cache


def reload() -> None:
    """Force a config reload (clears cache and re-reads from disk)."""
    global _cache, _cache_mtime
    _cache = None
    _cache_mtime = 0.0
    _load()


def raw() -> dict:
    """Return the raw config dict (cached, reloads on mtime change)."""
    return _load()


def get(section: str, key: str, default: Any = None) -> Any:
    """Get a top-level config value."""
    cfg = _load()
    return cfg.get(section, {}).get(key, default)


def get_int(section: str, key: str, default: int = 0) -> int:
    """Get an integer config value with a default."""
    val = get(section, key)
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


# Context compression parameters
def ctx_trigger_threshold() -> float:
    val = os.environ.get("CTX_TRIGGER_THRESHOLD")
    if val:
        return float(val)
    return float(get("context_compression", "trigger_threshold", 0.75))


def ctx_keep_recent_fraction() -> float:
    val = os.environ.get("CTX_KEEP_RECENT_FRAC")
    if val:
        return float(val)
    return float(get("context_compression", "keep_recent_fraction", 0.1))


def ctx_recent_hard_cap_frac() -> float:
    """Max fraction of current tokens the recent (unsummarized) section may
    occupy after user-anchor / rounds-based pullback. Prevents protecting a
    user message near the start of a long autonomous tool run from swallowing
    the entire context and making compression a no-op."""
    val = os.environ.get("CTX_RECENT_HARD_CAP_FRAC")
    if val:
        return float(val)
    return float(get("context_compression", "recent_hard_cap_fraction", 0.30))


def ctx_keep_recent_rounds() -> int:
    val = os.environ.get("CTX_KEEP_RECENT_ROUNDS")
    if val:
        return int(val)
    return int(get("context_compression", "keep_recent_rounds", 2))


def ctx_summary_max_tokens() -> int:
    val = os.environ.get("CTX_SUMMARY_MAX_TOKENS")
    if val:
        return int(val)
    return int(get("context_compression", "summary_max_tokens", 4000))


def ctx_conv_text_budget_chars() -> int:
    val = os.environ.get("CTX_CONV_TEXT_BUDGET_CHARS")
    if val:
        return int(val)
    return int(get("context_compression", "conv_text_budget_chars", 24000))


# VCS / Git
def vcs_git_server() -> str:
    val = os.environ.get("VCS_GIT_SERVER")
    if val:
        return val.rstrip("/")
    return get("vcs", "git_server", "").rstrip("/")


def vcs_default_remote() -> str:
    val = os.environ.get("VCS_DEFAULT_REMOTE")
    if val:
        return val
    return get("vcs", "default_remote", "origin")


def vcs_default_branch() -> str:
    val = os.environ.get("VCS_DEFAULT_BRANCH")
    if val:
        return val
    return get("vcs", "default_branch", "main")


# Node identity
def node_id() -> str:
    return get("node", "id", "node-local")


def node_label() -> str:
    return get("node", "label", "Local Node")


def node_register_to_gateway() -> bool:
    return bool(get("node", "register_to_gateway", True))


def node_secret() -> str:
    return get("auth", "node_secret", "")


def github_plugins_token() -> str:
    val = os.environ.get("GITHUB_PLUGINS_TOKEN")
    if val:
        return val
    return get("github", "plugins_token", "")


def project_root() -> str:
    """Return the active workspace directory (formerly project root)."""
    return _ws._WORKSPACE_ROOT


def skills_dir() -> str:
    val = os.environ.get("OPENSQUAD_SKILLS_DIR")
    if val:
        return os.path.abspath(val)
    cfg_val = get("skills_dir", "")
    if cfg_val:
        return os.path.abspath(cfg_val)
    return os.path.join(_ws._DEFAULT_ROOT, "skills")


def filesystem_workspace_dirs() -> list:
    return get("filesystem", "workspace_dirs", [])


def ensure_node_id() -> str:
    """Return node ID, auto-generating a UUID if still default."""
    global _cache
    cfg = _load()
    current_id = cfg.get("node", {}).get("id", "node-local")
    if current_id and current_id != "node-local":
        return current_id

    import uuid as _uuid
    new_id = str(_uuid.uuid4())
    cfg.setdefault("node", {})["id"] = new_id
    try:
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        _cache = cfg
        logger.info("[syscfg] Generated new node_id: %s", new_id)
    except Exception as e:
        logger.warning("[syscfg] Failed to persist node_id: %s", e)
    return new_id


def _is_placeholder_secret(value: object) -> bool:
    """Return True if ``value`` is a recognised auth-placeholder token.

    Placeholders are the values that ship in ``system_config.example.json``
    as a reminder to the user to replace them. They must never reach
    real network endpoints (Gateway / external adapters / feishu / telegram),
    because the adapter would 502 with an opaque "Agent not available"
    error and the user would have no way to tell that the auth layer
    was the actual failure (see issue #41).
    """
    if not isinstance(value, str):
        return False
    if not value:
        return True
    return value in {
        "YOUR_GATEWAY_TOKEN_HERE",
        "YOUR_NODE_SECRET_HERE",
        "YOUR_EXTERNAL_API_KEY_HERE",
        "opensquad-gateway-simple-token",
    }


def _auto_generate_secrets(target_path: str) -> bool:
    """Replace placeholder secrets in ``target_path`` with random tokens.

    Called from :func:`init_workspace` immediately after copying the
    config template, so that newly initialised workspaces never ship
    with placeholder auth values. Idempotent: a workspace whose
    ``auth.*`` fields are already real values is left untouched.

    The set of replaced keys is the union of all auth secrets the
    Gateway / external adapter / node registration check for:

    * ``auth.node_secret``         -- used by Agent runner registration
    * ``auth.gateway_token``       -- used by external adapters to call Gateway
    * ``auth.external_api_key``    -- used by the external adapter to auth Gateway -> external_api

    Returns ``True`` if any field was replaced, ``False`` otherwise.
    """
    try:
        with open(target_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        logger.warning("[syscfg] _auto_generate_secrets: cannot read %s: %s", target_path, e)
        return False

    auth = data.setdefault("auth", {})
    target_keys = ("node_secret", "gateway_token", "external_api_key")
    replaced = []
    for key in target_keys:
        if _is_placeholder_secret(auth.get(key, "")):
            import secrets as _secrets
            new_val = _secrets.token_urlsafe(32)
            auth[key] = new_val
            replaced.append(key)

    if not replaced:
        return False

    try:
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        for key in replaced:
            logger.info("[syscfg] Auto-generated auth.%s (replaced placeholder)", key)
    except OSError as e:
        logger.warning("[syscfg] _auto_generate_secrets: cannot write %s: %s", target_path, e)
        return False
    return True


def ensure_external_api_key() -> str:
    """Return external API key, auto-generating one if empty or placeholder."""
    global _cache
    cfg = _load()
    current_key = cfg.get("auth", {}).get("external_api_key", "")
    if current_key and not _is_placeholder_secret(current_key):
        return current_key

    import secrets as _secrets
    new_key = _secrets.token_urlsafe(32)
    cfg.setdefault("auth", {})["external_api_key"] = new_key
    try:
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        _cache = cfg
        logger.info("[syscfg] Generated new external_api_key")
    except Exception as e:
        logger.warning("[syscfg] Failed to persist external_api_key: %s", e)
    return new_key


def ensure_gateway_token() -> str:
    """Return ``auth.gateway_token``, auto-generating one if placeholder.

    Runtime fallback for workspaces that were initialised before
    :func:`_auto_generate_secrets` was added to :func:`init_workspace`
    (i.e. existing user installations that still have the placeholder
    value copied in by an old OpenSquad release). Mirrors the
    ``ensure_external_api_key()`` pattern; the Gateway at
    ``src/opensquad/gateway/backend/app/main.py`` short-circuits to
    "invalid token" on any placeholder, so 502s cascade silently
    until the token is real -- see issue #41.
    """
    global _cache
    cfg = _load()
    current = cfg.get("auth", {}).get("gateway_token", "")
    if current and not _is_placeholder_secret(current):
        return current

    import secrets as _secrets
    new_token = _secrets.token_urlsafe(32)
    cfg.setdefault("auth", {})["gateway_token"] = new_token
    try:
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        _cache = cfg
        logger.info("[syscfg] Generated new gateway_token (was placeholder)")
    except Exception as e:
        logger.warning("[syscfg] Failed to persist gateway_token: %s", e)
    return new_token


def ensure_node_secret() -> str:
    """Return ``auth.node_secret``, auto-generating one if placeholder.

    Used by Agent runner registration in local dev mode. A placeholder
    value is currently accepted (with a warning) by the registration
    check at ``websocket.py:67``, but we still want a real secret on
    disk so that the workspace is portable to a stricter production
    deployment without further edits.
    """
    global _cache
    cfg = _load()
    current = cfg.get("auth", {}).get("node_secret", "")
    if current and not _is_placeholder_secret(current):
        return current

    import secrets as _secrets
    new_secret = _secrets.token_urlsafe(32)
    cfg.setdefault("auth", {})["node_secret"] = new_secret
    try:
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        _cache = cfg
        logger.info("[syscfg] Generated new node_secret (was placeholder)")
    except Exception as e:
        logger.warning("[syscfg] Failed to persist node_secret: %s", e)
    return new_secret


# Cross-cutting workspace functions (moved here to avoid circular import with system_config)

def ensure_workspace_structure() -> None:
    """Ensure the workspace directory structure exists."""
    dirs = [
        _ws.workspace_metadata_dir(),
        _ws.workspace_data_dir("uploads"),
        _ws.workspace_data_dir("logs", "gateway"),
        _ws.workspace_sessions_dir(),
        _ws.workspace_data_dir("ai_his_talk"),
        _ws.workspace_data_dir("plugins"),
        _ws.workspace_data_dir("audit"),
        _ws.workspace_agents_dir(),
        _ws.workspace_gateway_dir("backend", "sessions"),
        _ws.workspace_gateway_dir("backend", "tasks"),
        _ws.workspace_gateway_dir("backend", "uploads"),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
    logger.info("[syscfg] Workspace structure ensured at: %s", _ws._WORKSPACE_ROOT)


def init_workspace(workspace_path: str, copy_config: bool = True) -> None:
    """Initialize a new workspace."""
    import datetime
    import shutil

    set_workspace(workspace_path)
    ensure_workspace_structure()

    workspace_meta = {
        "version": "1.0",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z",
        "opensquad_version": "1.2.3",
        "workspace_name": os.path.basename(workspace_path),
    }
    meta_path = _ws.workspace_metadata_dir("workspace.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(workspace_meta, f, indent=2, ensure_ascii=False)
    logger.info("[syscfg] Created workspace metadata: %s", meta_path)

    if copy_config:
        import shutil as _shutil
        for suffix in ["system_config.template.json", "system_config.json", "system_config.example.json"]:
            src = os.path.join(_ws._DEFAULT_ROOT, suffix)
            if os.path.exists(src):
                target = os.path.join(workspace_path, "system_config.json")
                _shutil.copy2(src, target)
                logger.info("[syscfg] Copied config template: %s", src)
                # Auto-replace placeholder secrets with generated random values.
                # This is the layer-A fix for issue #41: it prevents the
                # gateway_token / node_secret placeholders from ever being
                # persisted to a fresh workspace, so adapters that read
                # system_config.json at startup never have to second-guess
                # whether the value is real. Idempotent for workspaces that
                # already have real values (no-op).
                _auto_generate_secrets(target)
                break


def is_service_enabled(plugin_name: str) -> bool:
    """Check if a plugin service is enabled. Uses cached _load() to avoid disk I/O on every call."""
    try:
        cfg = _load()
        return cfg.get("services", {}).get(plugin_name, {}).get("enabled", True)
    except Exception:
        return False
