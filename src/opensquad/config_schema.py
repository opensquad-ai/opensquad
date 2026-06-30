"""AgentConfig Schema — Pydantic validation for config.json (Config Validation).

Validates agent configuration at boot time, catching misconfigurations
before they cause runtime errors.

Usage:
    from opensquad.config_schema import validate_agent_config
    try:
        validated = validate_agent_config(raw_dict)
    except ConfigValidationError as e:
        print(f"Config error: {e}")
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)


class ModelConfigSchema(BaseModel):
    """Schema for the ``model`` section of config.json."""

    schema_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")
    # api_protocol: API 协议类型 (openai / openai_compat / claude / anthropic / google / gemini)
    api_protocol: str = Field(
        default="openai_compat", pattern=r"^(openai|openai_compat|claude|anthropic|google|gemini)$"
    )
    # provider: 模型供应商名称（厂商），仅用于 UI 展示/分组
    provider: str = Field(default="")
    model_name: str = Field(default="", min_length=1)
    api_key: str = Field(default="")
    base_url: str = Field(default="")
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    token_max: int = Field(default=100000, gt=0)
    timeout: float = Field(default=120.0, gt=0)
    tool_call_mode: str = Field(default="auto", pattern=r"^(auto|native|xml)$")
    is_image: bool = Field(default=False)
    is_video: bool = Field(default=False)
    is_audio_model: bool = Field(default=False)
    use_file_api: bool = Field(default=False)
    file_api_size_threshold: int = Field(default=4 * 1024 * 1024, gt=0)
    is_audio_output: bool = Field(default=False)
    audio_output_voice: str = Field(default="alloy")
    frequency_penalty: float = Field(default=0.0, ge=-2.0, le=2.0)
    presence_penalty: float = Field(default=0.0, ge=-2.0, le=2.0)
    enable_repetition_check: bool = Field(default=False)
    max_video_frames: int = Field(default=8, ge=1, le=20)
    is_think: bool = Field(default=False)
    thinking_budget_tokens: int = Field(default=10000, gt=0)
    is_image_output: bool = Field(default=False)
    top_k: int = Field(default=0, ge=0)

    @field_validator("model_name")
    @classmethod
    def model_name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("model_name cannot be empty")
        return v.strip()


class PromptConfigSchema(BaseModel):
    """Schema for the ``prompt`` section of config.json."""

    base: str | None = Field(default=None)
    role: str = Field(default="role.md")


class CollaborationConfigSchema(BaseModel):
    """Schema for the ``collaboration`` section of config.json."""

    enabled: bool = Field(default=False)
    team_id: str | None = Field(default=None)


class GatewayConfigSchema(BaseModel):
    """Schema for the ``gateway`` section of config.json."""

    enabled: bool = Field(default=True)
    url: str = Field(default="")


class WebServerConfigSchema(BaseModel):
    """Schema for the ``web_server`` section of config.json."""

    enabled: bool = Field(default=False)
    port: int = Field(default=0, ge=0, le=65535)


class GroupChatConfigSchema(BaseModel):
    """Schema for the ``group_chat`` section of config.json."""

    enabled: bool = Field(default=True)
    email: str = Field(default="ai@ai")
    password: str = Field(default="aaaaaa")
    groups: list[str] = Field(default_factory=list)
    base_url: str | None = Field(default=None)


class AgentConfigSchema(BaseModel):
    """Top-level schema for config.json."""

    model_config = ConfigDict(extra="allow")
    schema_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")
    agent_id: str = Field(default="", min_length=1)
    agent_name: str = Field(default="", min_length=1)
    agent_type: str = Field(default="assistant")
    capabilities: list[str] = Field(default_factory=list)
    model: ModelConfigSchema = Field(default_factory=ModelConfigSchema)
    prompt: PromptConfigSchema = Field(default_factory=PromptConfigSchema)
    tools: list[str] = Field(default_factory=list)
    plugins: list[str] = Field(default_factory=list)
    tool_levels: dict[str, Any] = Field(default_factory=dict)
    collaboration: CollaborationConfigSchema = Field(default_factory=CollaborationConfigSchema)
    gateway: GatewayConfigSchema = Field(default_factory=GatewayConfigSchema)
    web_server: WebServerConfigSchema = Field(default_factory=WebServerConfigSchema)
    group_chat: GroupChatConfigSchema = Field(default_factory=GroupChatConfigSchema)
    mcp_servers: dict[str, Any] = Field(default_factory=dict)
    system_tools: dict[str, Any] = Field(default_factory=dict)
    state_machine: dict[str, Any] | None = Field(default=None)
    load_his: str | None = Field(default=None)
    prompt_preload: dict[str, Any] | None = Field(default=None)

    @field_validator("agent_id")
    @classmethod
    def agent_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("agent_id cannot be empty")
        return v.strip()

    @field_validator("agent_name")
    @classmethod
    def agent_name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("agent_name cannot be empty")
        return v.strip()


class ConfigValidationError(Exception):
    """Raised when config.json fails validation."""

    pass


_CONFIG_SCHEMA_VERSION = "1.0"


def validate_agent_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a raw config.json dict.

    Args:
        raw: The dict loaded from config.json.

    Returns:
        The validated dict (with defaults filled in).

    Raises:
        ConfigValidationError: If validation fails, with human-readable message.
    """
    # Schema version check and auto-migration
    version = raw.get("schema_version", "0.9")
    if version == "0.9":
        raw = _migrate_v0_9_to_v1_0(raw)
    elif version != _CONFIG_SCHEMA_VERSION:
        raise ConfigValidationError(f"Unsupported config schema_version: {version}. Expected: {_CONFIG_SCHEMA_VERSION}")

    try:
        validated = AgentConfigSchema(**raw)
        return validated.model_dump()
    except ValidationError as e:
        # Build human-readable error message
        lines = ["config.json validation failed:"]
        for err in e.errors():
            loc = " -> ".join(str(x) for x in err["loc"])
            msg = err["msg"]
            lines.append(f"  [{loc}] {msg}")
        raise ConfigValidationError("\n".join(lines)) from e
    except Exception as e:
        raise ConfigValidationError(f"Unexpected config validation error: {e}") from e


def _migrate_v0_9_to_v1_0(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate schema_version 0.9 config to 1.0.

    - Adds schema_version field
    - Ensures required fields have defaults
    """
    migrated = dict(raw)
    migrated["schema_version"] = "1.0"
    # Ensure all required AgentConfigSchema fields exist with sensible defaults
    migrated.setdefault("agent_id", migrated.get("agent_id", ""))
    migrated.setdefault("agent_name", migrated.get("agent_name", ""))
    if "model" not in migrated:
        migrated["model"] = {}
    if "prompt" not in migrated:
        migrated["prompt"] = {"role": "role.md"}
    logger.info("[Config] Auto-migrated config schema v0.9 → v1.0")
    return migrated
