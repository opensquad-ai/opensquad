"""
_syscfg/_models.py — Typed configuration models (Pydantic v2).

Provides a ``SystemConfig`` root model that mirrors the schema of
``system_config.json``.  All access goes through typed fields instead of
``get(section, key)`` dict lookups, giving IDE autocomplete and runtime
type validation.

Usage:

    from opensquad._syscfg._models import SystemConfig

    cfg = SystemConfig.from_file("/path/to/system_config.json")
    cfg.auth.node_secret         # str
    cfg.services["feishu"].enabled  # bool

Migration: new code should prefer ``SystemConfig`` over ``raw()`` / ``get()``.
The legacy functions in ``_config.py`` remain for backward compatibility.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

# ── Nested models ───────────────────────────────────────────────────────────


class HostsConfig(BaseModel, extra="forbid"):
    """Top-level ``hosts`` section."""

    gateway: str = "127.0.0.1"
    ai_web: str = "127.0.0.1"


class PortsConfig(BaseModel, extra="forbid"):
    """Top-level ``ports`` section.

    Defaults are aligned with ``_syscfg._network.port()`` so the typed model and
    the runtime resolver agree on the same gateway port (9555).
    """

    gateway: int = 9555
    launcher: int = 9600
    plugin_registry: int = 9720
    frontend: int = 5173
    external_adapter: int = 9700


class SecurityConfig(BaseModel, extra="forbid"):
    """Top-level ``security`` section.

    ``cors_allow_origins`` is a legacy field; ``gateway.cors_origins`` takes
    precedence at runtime. Default to ``["*"]`` so a fresh deployment works
    from any origin (LAN IP, Electron, production domain) without manual CORS
    configuration. See issue #43 for the diagnosis that led to this default.
    """

    cors_allow_origins: list[str] = Field(default_factory=lambda: ["*"])


class AuthConfig(BaseModel, extra="forbid"):
    """Top-level ``auth`` section."""

    node_secret: str = ""
    gateway_token: str = ""
    external_api_key: str = ""


class JwtConfig(BaseModel, extra="forbid"):
    """Top-level ``jwt`` section."""

    secret_key: str = "CHANGE_ME"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440


class NodeConfig(BaseModel, extra="forbid"):
    """Top-level ``node`` section."""

    id: str = ""
    label: str = ""
    register_to_gateway: bool = False


class ServiceConfig(BaseModel, extra="forbid"):
    """Single entry in ``services`` section."""

    enabled: bool = True
    auto_start: bool = False


# ── Root model ──────────────────────────────────────────────────────────────


class SystemConfig(BaseModel, extra="ignore"):
    """Typed root model for ``system_config.json``.

    Every top-level section is represented as a nested Pydantic model
    with sensible defaults, so accessing any path is safe.

    Fields not declared here are silently ignored (``extra="ignore"``)
    to preserve forward compatibility.
    """

    hosts: HostsConfig = Field(default_factory=HostsConfig)
    ports: PortsConfig = Field(default_factory=PortsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    jwt: JwtConfig = Field(default_factory=JwtConfig)
    node: NodeConfig = Field(default_factory=NodeConfig)
    services: dict[str, ServiceConfig] = Field(default_factory=dict)

    # ── Factories ───────────────────────────────────────────────────────

    @classmethod
    def from_file(cls, path: str) -> SystemConfig:
        """Load from a JSON file path.  Returns all-defaults when missing."""
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        return cls(**data)

    @classmethod
    def from_dict(cls, data: dict) -> SystemConfig:
        """Build from a raw dict (same format as ``raw()`` returns)."""
        return cls(**data)

    # ── Convenience accessors ───────────────────────────────────────────

    def is_service_enabled(self, plugin_name: str) -> bool:
        """Check if a plugin service is enabled (default: True)."""
        svc = self.services.get(plugin_name)
        return svc.enabled if svc is not None else True
