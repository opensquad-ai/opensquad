"""
Feishu Bot Adapter Configuration (Multi-bot)

Reads from system_config.json feishu section.
Supports multiple bot instances, each with its own credentials and agent binding.

Config structure in system_config.json:
  "feishu": {
    "log_level": "INFO",
    "request_timeout": 60,
    "bots": [
      {"name": "...", "app_id": "...", "app_secret": "...", "agent_id": "coder-001", "enabled": true},
      {"name": "...", "app_id": "...", "app_secret": "...", "agent_id": "pm-001",    "enabled": true}
    ]
  }
"""

import json
import os
import sys
from dataclasses import asdict, dataclass

# plugins/feishu/ -> plugins/ -> project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from opensquad.system_config import _CONFIG_PATH, syscfg


@dataclass
class FeishuBotConfig:
    """Single Feishu bot instance config."""

    name: str
    app_id: str
    app_secret: str
    agent_id: str
    encrypt_key: str = ""
    verification_token: str = ""
    enabled: bool = True
    request_timeout: int = 60


# ── Section-level shared config ──
FEISHU_LOG_LEVEL: str = syscfg.get("feishu", "log_level", "INFO")
FEISHU_DEFAULT_TIMEOUT: int = syscfg.get_int("feishu", "request_timeout", 60)


def is_service_enabled() -> bool:
    """Check if feishu service is enabled."""
    return syscfg.is_service_enabled("feishu")


# ── External Adapter Connection ──
EXTERNAL_ADAPTER_URL: str = os.environ.get("EXTERNAL_ADAPTER_URL") or syscfg.external_adapter_url()
# ensure_external_api_key() (added in issue #41 fix) writes a real key
# if the workspace still carries the placeholder value, mirroring the
# runtime-fallback path the other auth tokens use.
EXTERNAL_API_KEY: str = syscfg.ensure_external_api_key()
if not EXTERNAL_API_KEY or EXTERNAL_API_KEY == "YOUR_EXTERNAL_API_KEY_HERE":
    import logging as _logging

    _logging.getLogger(__name__).error(
        "auth.external_api_key is still the placeholder after ensure_external_api_key(). "
        "Feishu -> external_api -> Gateway will fail with 502 on every inbound message. "
        "Set a real key in system_config.json -> auth.external_api_key."
    )


def _bots_from_raw(raw_bots) -> list[FeishuBotConfig]:
    """Convert raw bot dicts to FeishuBotConfig objects (no enabled check)."""
    configs = []
    for b in raw_bots:
        if not b.get("enabled", True):
            continue
        app_id = b.get("app_id", "")
        app_secret = b.get("app_secret", "")
        if not app_id or not app_secret:
            continue
        configs.append(
            FeishuBotConfig(
                name=b.get("name", f"feishu-bot-{len(configs) + 1}"),
                app_id=app_id,
                app_secret=app_secret,
                agent_id=b.get("agent_id", "default-001"),
                encrypt_key=b.get("encrypt_key", ""),
                verification_token=b.get("verification_token", ""),
                enabled=True,
                request_timeout=b.get("request_timeout", FEISHU_DEFAULT_TIMEOUT),
            )
        )
    return configs


def load_bot_configs() -> list[FeishuBotConfig]:
    """Load all enabled bot configs from system_config.json (cached syscfg)."""
    raw_bots = syscfg.get("feishu", "bots", [])
    return _bots_from_raw(raw_bots)


def load_bot_configs_fresh() -> list[FeishuBotConfig]:
    """Re-read system_config.json from disk, bypassing the syscfg cache.

    Used by the config watcher to detect edits made by the Web UI / launcher.
    """
    try:
        with open(_CONFIG_PATH, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    raw_bots = cfg.get("feishu", {}).get("bots", [])
    return _bots_from_raw(raw_bots)


def bot_config_to_json(bot: FeishuBotConfig) -> str:
    """Serialize a single bot config to a JSON string for subprocess env var."""
    return json.dumps(asdict(bot), ensure_ascii=False)


def bot_config_from_env(env_var: str = "FEISHU_BOT_CONFIG_JSON") -> FeishuBotConfig | None:
    """Read a single bot config from a subprocess env var (set by orchestrator)."""
    raw = os.environ.get(env_var, "")
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return FeishuBotConfig(**d)
    except Exception:
        return None
