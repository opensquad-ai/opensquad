"""
_syscfg/_network.py -- Network config (ports, hosts, URLs, auth).

Extracted from system_config.py.
"""

from __future__ import annotations

import os

from ._config import get, get_int


def _client_host(service: str = "gateway") -> str:
    """
    Resolve the host for client connections.
    Converts 0.0.0.0 to 127.0.0.1 for localhost connectivity.
    """
    h = get(service, "host", "0.0.0.0")
    if h == "0.0.0.0":
        return "127.0.0.1"
    if h == "::":
        return "::1"
    return h


def client_host(service: str = "gateway") -> str:
    """Public alias for _client_host."""
    return _client_host(service)


def port(name: str) -> int:
    """Get a service port from config. env > json > defaults."""
    env_map = {
        "gateway": "PORT_GATEWAY",
        "launcher": "PORT_LAUNCHER",
        "gateway_tunnel": "PORT_GATEWAY_TUNNEL",
    }
    env_key = env_map.get(name, f"PORT_{name.upper()}")
    val = os.environ.get(env_key)
    if val:
        return int(val)
    defaults = {
        "gateway": 9555,
        "launcher": 9600,
        "health": 9999,
        "plugin_registry": 9720,
        "registry": 9720,
        "frontend": 5173,
        "external_adapter": 9700,
        "websearch": 9001,
        "whisper": 5001,
    }
    return get_int("ports", name, defaults.get(name, 0))


def host(name: str) -> str:
    """Get a service host from config. env > json > default."""
    return get("hosts", name, "0.0.0.0")


def cors_origins() -> list[str]:
    """Get CORS allowed origins from config.

    Reads from ``gateway.cors_origins`` in system_config.json.
    Falls back to ``security.cors_allow_origins`` for backward compatibility.
    If neither is configured, returns ``["*"]``.

    The ``["*"]`` default is a deliberate choice for an open-source project:
    a fresh deployment is expected to work from any origin (LAN IP, Electron
    ``file://``, production domain), not just localhost dev. The previous
    ``["http://localhost:5173"]`` default silently broke every non-local
    access with an opaque ``TypeError: Failed to fetch`` that took hours to
    diagnose (see issue #43).

    Trade-off: with ``["*"]``, ``allow_credentials`` is forced to ``False``
    by the CORS spec, so cookie-based auth cannot be used. The existing
    FastAPI middleware already handles this correctly. Production deployments
    that need credentials should explicitly set ``gateway.cors_origins`` to
    a specific origin list.
    """
    origins = get("gateway", "cors_origins", None)
    if origins is not None:
        return origins
    # Backward compatibility: check security.cors_allow_origins
    origins = get("security", "cors_allow_origins", None)
    if origins is not None:
        return origins
    return ["*"]


def auth(name: str) -> str:
    """Get an auth key / token from config. env > json."""
    env_key = f"{name.upper()}_KEY"
    val = os.environ.get(env_key)
    if val:
        return val
    return get("auth", name, "")


def gateway_http() -> str:
    """HTTP URL for the gateway."""
    h = _client_host("gateway")
    p = port("gateway")
    return f"http://{h}:{p}"


def gateway_ws() -> str:
    """WebSocket URL for the gateway."""
    h = _client_host("gateway")
    p = port("gateway")
    return f"ws://{h}:{p}"


def gateway_register_url() -> str:
    """Agent registration WebSocket URL."""
    h = _client_host("gateway")
    p = port("gateway")
    return f"ws://{h}:{p}/ai-ws/register"


def launcher_url() -> str:
    """HTTP URL for the launcher management server."""
    h = _client_host("launcher")
    p = port("launcher")
    return f"http://{h}:{p}"


def external_adapter_url() -> str:
    """HTTP URL for the external adapter.

    Priority:
      1. explicit ``external_adapter.url`` in system_config.json
      2. ``ports.external_adapter`` (default 9700) aligned with external_api plugin
      3. legacy fallback ``http://127.0.0.1:9300``
    """
    explicit = get("external_adapter", "url", None)
    if explicit:
        return explicit
    h = _client_host("external_adapter")
    p = port("external_adapter")
    return f"http://{h}:{p}"


def default_agent_id() -> str:
    """Default agent ID."""
    return os.environ.get("DEFAULT_AGENT_ID") or get("defaults", "agent_id", "pm-001")


def default_timeout() -> int:
    """Default request timeout in seconds."""
    val = os.environ.get("EXTERNAL_TIMEOUT")
    if val:
        return int(val)
    return get_int("defaults", "request_timeout", 120)


def async_result_ttl() -> int:
    """Async result TTL in seconds."""
    val = os.environ.get("ASYNC_RESULT_TTL")
    if val:
        return int(val)
    return get_int("defaults", "async_result_ttl", 600)


def whisper_url() -> str:
    """Whisper service URL (aligned with ``port(\"whisper\")``, default 5001)."""
    explicit = get("services", "whisper_url", None)
    if explicit:
        return explicit
    return f"http://127.0.0.1:{port('whisper')}"


def websearch_url() -> str:
    """Web search service URL (aligned with ``port(\"websearch\")``, default 9001)."""
    explicit = get("services", "websearch_url", None)
    if explicit:
        return explicit
    return f"http://127.0.0.1:{port('websearch')}"
