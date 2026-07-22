"""
Self-contained runtime helpers for plugin SERVICE processes.

Plugin services (websearch, whisper, external_api, feishu, telegram) are
spawned by the launcher as subprocesses using the *Agent Python* — an
embeddable Python that lives in
``%LOCALAPPDATA%/OpenSquad/runtime/python311/python.exe``.  That Python does
NOT have the ``opensquad`` package installed: ``opensquad`` is compiled into
the frozen ``run.exe`` PYZ archive and is not importable from external
processes.

Therefore service scripts (and their config modules) MUST NOT do
``from opensquad.system_config import syscfg``.  This module provides the
minimal subset those scripts need, using only:

  * environment variables (``OPENSQUAD_WORKSPACE`` /
    ``OPENSQUAD_USER_DATA`` / ``OPENSQUAD_APP_DATA``, always set by the
    launcher in ``_build_child_process_env``)
  * direct JSON reads of ``<workspace>/system_config.json``

It is shipped BOTH as a loose file at ``_internal/plugins/_service_runtime.py``
(so the Agent Python service process can import it) AND inside the PYZ
archive (so the frozen ``run.exe`` agent process can import it).  Importing
the parent ``plugins`` package is safe — ``plugins/__init__.py`` only imports
``typing`` at module level.
"""

from __future__ import annotations

import json
import os
import secrets
from typing import Any

# ── Workspace resolution ───────────────────────────────────────────────
# Priority mirrors opensquad._syscfg._workspace._resolve_initial_workspace().


def get_workspace() -> str:
    """Return the active workspace directory (from env vars set by launcher)."""
    for key in ("OPENSQUAD_WORKSPACE", "OPENSQUAD_USER_DATA", "OPENSQUAD_APP_DATA"):
        raw = os.environ.get(key, "").strip()
        if raw:
            return os.path.abspath(raw)
    # Last-resort fallback: 2 levels up from this file
    # (plugins/_service_runtime.py -> plugins/ -> project root / _internal).
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path() -> str:
    """Return the path to system_config.json in the active workspace."""
    return os.path.join(get_workspace(), "system_config.json")


# ── Config loading (cached, mtime-aware) ───────────────────────────────

_cache: dict | None = None
_cache_mtime: float = 0.0
_cache_path: str = ""


def reload() -> None:
    """Force a config reload (clears cache)."""
    global _cache, _cache_mtime, _cache_path
    _cache = None
    _cache_mtime = 0.0
    _cache_path = ""


def _load_config() -> dict:
    """Load & cache system_config.json with auto-reload on file change."""
    global _cache, _cache_mtime, _cache_path
    path = config_path()
    mtime = 0.0
    try:
        if os.path.exists(path):
            mtime = os.path.getmtime(path)
    except OSError:
        pass
    if _cache is not None and _cache_path == path and mtime == _cache_mtime:
        return _cache
    try:
        with open(path, encoding="utf-8-sig") as f:
            _cache = json.load(f)
    except (OSError, json.JSONDecodeError):
        _cache = {}
    _cache_path = path
    _cache_mtime = mtime
    return _cache or {}


def raw() -> dict:
    """Return the raw config dict (cached, reloads on mtime change)."""
    return _load_config()


def get(section: str, key: str, default: Any = None) -> Any:
    """Get a top-level config value: cfg[section][key]."""
    return _load_config().get(section, {}).get(key, default)


def get_int(section: str, key: str, default: int = 0) -> int:
    """Get an integer config value with a default."""
    val = get(section, key)
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


# ── Path builders (mirror opensquad._syscfg._workspace) ────────────────


def workspace_data_dir(*subpaths: str) -> str:
    """Return <workspace>/data/<subpaths>."""
    return os.path.join(get_workspace(), "data", *subpaths)


def workspace_logs_dir(*subpaths: str) -> str:
    """Return <workspace>/data/logs/<subpaths>."""
    return os.path.join(workspace_data_dir("logs"), *subpaths)


def workspace_agents_dir(*subpaths: str) -> str:
    """Return <workspace>/agents/<subpaths>."""
    return os.path.join(get_workspace(), "agents", *subpaths)


def workspace_plugins_dir(*subpaths: str) -> str:
    """Return <workspace>/plugins/<subpaths>."""
    return os.path.join(get_workspace(), "plugins", *subpaths)


# ── Network (mirror opensquad._syscfg._network) ────────────────────────

_DEFAULT_PORTS = {
    "gateway": 9555,
    "launcher": 9600,
    "health": 9999,
    "plugin_registry": 9720,
    "registry": 9720,
    "frontend": 5173,
    "external_adapter": 9700,
    "websearch": 9001,
    "whisper": 5001,
    "sensevoice": 7101,
}

_ENV_PORT_MAP = {
    "gateway": "PORT_GATEWAY",
    "launcher": "PORT_LAUNCHER",
    "gateway_tunnel": "PORT_GATEWAY_TUNNEL",
}


def port(name: str) -> int:
    """Get a service port: env > json > defaults."""
    env_key = _ENV_PORT_MAP.get(name, f"PORT_{name.upper()}")
    val = os.environ.get(env_key)
    if val:
        try:
            return int(val)
        except ValueError:
            pass
    return get_int("ports", name, _DEFAULT_PORTS.get(name, 0))


def host(name: str) -> str:
    """Get a service host: json > '0.0.0.0'."""
    return get("hosts", name, "0.0.0.0")


def client_host(name: str = "gateway") -> str:
    """Resolve the host for client connections (0.0.0.0 -> 127.0.0.1)."""
    h = host(name)
    if h == "0.0.0.0":
        return "127.0.0.1"
    if h == "::":
        return "::1"
    return h


def gateway_http() -> str:
    h = client_host("gateway")
    return f"http://{h}:{port('gateway')}"


def gateway_ws() -> str:
    h = client_host("gateway")
    return f"ws://{h}:{port('gateway')}"


def launcher_url() -> str:
    h = client_host("launcher")
    return f"http://{h}:{port('launcher')}"


def external_adapter_url() -> str:
    """HTTP URL for the external adapter."""
    explicit = get("external_adapter", "url", None)
    if explicit:
        return explicit
    h = client_host("external_adapter")
    return f"http://{h}:{port('external_adapter')}"


def whisper_url() -> str:
    explicit = get("services", "whisper_url", None)
    if explicit:
        return explicit
    return f"http://127.0.0.1:{port('whisper')}"


def sensevoice_url() -> str:
    explicit = get("services", "sensevoice_url", None)
    if explicit:
        return explicit
    return f"http://127.0.0.1:{port('sensevoice')}"


def websearch_url() -> str:
    explicit = get("services", "websearch_url", None)
    if explicit:
        return explicit
    return f"http://127.0.0.1:{port('websearch')}"


# ── Auth ───────────────────────────────────────────────────────────────
# The workspace is writable (%LOCALAPPDATA%/OpenSquad/workspace), so the
# ensure_* helpers can persist generated tokens — matching the behaviour of
# opensquad._syscfg._config.  The launcher runs ensure_workspace_config_file()
# at startup (which calls _auto_generate_secrets), so by the time services
# start these are normally no-ops; they remain as a belt-and-suspenders net.

_PLACEHOLDER_SECRETS = {
    "YOUR_GATEWAY_TOKEN_HERE",
    "YOUR_NODE_SECRET_HERE",
    "YOUR_EXTERNAL_API_KEY_HERE",
    "opensquad-gateway-simple-token",
}


def _is_placeholder(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if not value:
        return True
    return value in _PLACEHOLDER_SECRETS


def auth(name: str) -> str:
    """Get an auth key/token: env > json."""
    env_key = f"{name.upper()}_KEY"
    val = os.environ.get(env_key)
    if val:
        return val
    return get("auth", name, "")


def _ensure_secret(key: str) -> str:
    """Return auth.<key>, generating & persisting a real token if placeholder."""
    cfg = _load_config()
    current = cfg.get("auth", {}).get(key, "")
    if current and not _is_placeholder(current):
        return current
    new_val = secrets.token_urlsafe(32)
    cfg.setdefault("auth", {})[key] = new_val
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        reload()
    except OSError:
        # Read-only or locked: return the generated value anyway so the
        # service can proceed in-memory (it will just regenerate next run).
        pass
    return new_val


def ensure_gateway_token() -> str:
    return _ensure_secret("gateway_token")


def ensure_external_api_key() -> str:
    return _ensure_secret("external_api_key")


def ensure_node_secret() -> str:
    return _ensure_secret("node_secret")


# ── Misc helpers used by services ──────────────────────────────────────


def is_service_enabled(plugin_name: str) -> bool:
    """Check if a plugin service is enabled via services.<name>.enabled."""
    try:
        return _load_config().get("services", {}).get(plugin_name, {}).get("enabled", True)
    except Exception:
        return False


def default_timeout() -> int:
    val = os.environ.get("EXTERNAL_TIMEOUT")
    if val:
        return int(val)
    return get_int("defaults", "request_timeout", 120)


def async_result_ttl() -> int:
    val = os.environ.get("ASYNC_RESULT_TTL")
    if val:
        return int(val)
    return get_int("defaults", "async_result_ttl", 600)


def default_agent_id() -> str:
    return os.environ.get("DEFAULT_AGENT_ID") or get("defaults", "agent_id", "pm-001")
