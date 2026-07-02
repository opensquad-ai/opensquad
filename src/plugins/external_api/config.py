"""
External Adapter Configuration (Multi-instance)

Reads from system_config.json external_api section.
Supports multiple adapter instances, each with its own port, API key,
and default agent binding.

Config structure in system_config.json:
  "external_api": {
    "log_level": "INFO",
    "instances": [
      {"name": "default", "host": "0.0.0.0", "port": 9700, "default_agent_id": "coder-001",
       "api_key": "key1", "request_timeout": 120, "async_result_ttl": 600, "enabled": true},
      {"name": "pm-api",  "host": "0.0.0.0", "port": 9701, "default_agent_id": "pm-001",
       "api_key": "key2", "request_timeout": 120, "async_result_ttl": 600, "enabled": true}
    ]
  }
"""

import os
import secrets
import sys
from dataclasses import dataclass

# Ensure project root is importable.
# In frozen mode, APPEND (not insert(0)) so the Agent Python's site-packages
# wins over _internal/ loose copies of third-party packages whose transitive
# deps live only in the PYZ archive. See adapter.py for full rationale.
_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    if getattr(sys, "frozen", False):
        sys.path.append(_root)
    else:
        sys.path.insert(0, _root)
# Self-contained runtime helper — does NOT import opensquad (which is not
# available to the Agent Python that runs plugin services in frozen mode).
from plugins._service_runtime import (
    async_result_ttl as _async_result_ttl,
)
from plugins._service_runtime import (
    auth as _auth,
)
from plugins._service_runtime import (
    client_host as _client_host,
)
from plugins._service_runtime import (
    default_timeout as _default_timeout,
)
from plugins._service_runtime import (
    ensure_gateway_token as _ensure_gateway_token,
)
from plugins._service_runtime import (
    get as _get,
)
from plugins._service_runtime import (
    host as _host,
)
from plugins._service_runtime import (
    port as _port,
)


@dataclass
class ExternalApiInstanceConfig:
    """Single External API adapter instance config."""

    name: str
    host: str
    port: int
    api_key: str
    auto_generated_key: bool
    request_timeout: int
    async_result_ttl: int
    enabled: bool = True


# ── Gateway connection (shared across all instances) ──
# Use _client_host which handles 0.0.0.0 -> 127.0.0.1 conversion for client connections
GATEWAY_HOST: str = _client_host("gateway")
GATEWAY_PORT: int = _port("gateway")
GATEWAY_WS_URL: str = f"ws://{GATEWAY_HOST}:{GATEWAY_PORT}/ai-web/ws"
# Use the ensure_ helper so workspaces that pre-date issue #41's fix and
# still carry the "YOUR_GATEWAY_TOKEN_HERE" placeholder get a real token
# at module-load time. Without this, every chat request would 502 with
# an opaque "Agent not available" and the user would have no way to
# know the auth layer was the failure mode.
GATEWAY_TOKEN: str = _ensure_gateway_token()
# Loud warning if ensure_ failed (e.g. workspace is read-only); the user
# needs to know explicitly because the silent failure mode is a 502.
if not GATEWAY_TOKEN or GATEWAY_TOKEN == "YOUR_GATEWAY_TOKEN_HERE":
    import logging as _logging

    _logging.getLogger(__name__).error(
        "auth.gateway_token is still the placeholder after ensure_gateway_token(). "
        "external_api will fail to authenticate with the Gateway; every chat "
        "request will return 502. Set a real token in system_config.json -> auth.gateway_token."
    )

# ── Log level ──
EXTERNAL_API_LOG_LEVEL: str = _get("external_api", "log_level", "INFO")


def load_instance_configs() -> list[ExternalApiInstanceConfig]:
    """Load all enabled instance configs from system_config.json."""
    raw_instances = _get("external_api", "instances", [])
    configs = []

    for inst in raw_instances:
        if not inst.get("enabled", True):
            continue

        api_key = inst.get("api_key", "") or _auth("external_api_key")
        auto_generated = False
        if not api_key or api_key == "YOUR_EXTERNAL_API_KEY_HERE":
            api_key = secrets.token_urlsafe(32)
            auto_generated = True

        configs.append(
            ExternalApiInstanceConfig(
                name=inst.get("name", f"instance-{len(configs) + 1}"),
                host=inst.get("host", "0.0.0.0"),
                port=inst.get("port", 9700 + len(configs)),
                api_key=api_key,
                auto_generated_key=auto_generated,
                request_timeout=inst.get("request_timeout", 120),
                async_result_ttl=inst.get("async_result_ttl", 600),
                enabled=True,
            )
        )

    # Fallback: if no instances configured, build one from legacy config
    if not configs:
        api_key = _auth("external_api_key")
        auto_generated = False
        if not api_key or api_key == "YOUR_EXTERNAL_API_KEY_HERE":
            api_key = secrets.token_urlsafe(32)
            auto_generated = True
        configs.append(
            ExternalApiInstanceConfig(
                name="default",
                host=_host("external_adapter"),
                port=_port("external_adapter"),
                api_key=api_key,
                auto_generated_key=auto_generated,
                request_timeout=_default_timeout(),
                async_result_ttl=_async_result_ttl(),
            )
        )

    return configs
