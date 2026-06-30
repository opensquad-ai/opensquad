"""
Telegram Bot Adapter Configuration (Multi-bot)

Reads from system_config.json telegram section.
Supports multiple bot instances, each with its own token and agent binding.

Config structure in system_config.json:
  "telegram": {
    "log_level": "INFO",
    "request_timeout": 60,
    "connect_timeout": 30,
    "proxy": "",
    "bots": [
      {"name": "...", "bot_token": "...", "agent_id": "coder-001", "enabled": true},
      {"name": "...", "bot_token": "...", "agent_id": "pm-001",    "enabled": true}
    ]
  }
"""

import os
import sys
from dataclasses import dataclass

# plugins/telegram/ -> plugins/ -> project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from opensquad.system_config import syscfg


@dataclass
class TelegramBotConfig:
    """Single Telegram bot instance config."""

    name: str
    bot_token: str
    agent_id: str
    enabled: bool = True
    # per-bot overrides (fallback to section-level defaults)
    request_timeout: int = 60
    connect_timeout: int = 30
    proxy: str = ""


# ── Section-level shared config ──
TELEGRAM_LOG_LEVEL: str = syscfg.get("telegram", "log_level", "INFO")
TELEGRAM_DEFAULT_TIMEOUT: int = syscfg.get_int("telegram", "request_timeout", 60)
TELEGRAM_DEFAULT_CONNECT_TIMEOUT: int = syscfg.get_int("telegram", "connect_timeout", 30)
TELEGRAM_DEFAULT_PROXY: str = syscfg.get("telegram", "proxy", "") or os.environ.get("TELEGRAM_PROXY", "")


def is_service_enabled() -> bool:
    """Check if telegram service is enabled."""
    return syscfg.is_service_enabled("telegram")


# ── External Adapter Connection ──
EXTERNAL_ADAPTER_URL: str = os.environ.get("EXTERNAL_ADAPTER_URL") or syscfg.external_adapter_url()
EXTERNAL_API_KEY: str = syscfg.auth("external_api_key")


def load_bot_configs() -> list[TelegramBotConfig]:
    """Load all enabled bot configs from system_config.json."""
    raw_bots = syscfg.get("telegram", "bots", [])
    configs = []
    for b in raw_bots:
        if not b.get("enabled", True):
            continue
        token = b.get("bot_token", "")
        if not token:
            continue
        configs.append(
            TelegramBotConfig(
                name=b.get("name", f"bot-{len(configs) + 1}"),
                bot_token=token,
                agent_id=b.get("agent_id", "default-001"),
                enabled=True,
                request_timeout=b.get("request_timeout", TELEGRAM_DEFAULT_TIMEOUT),
                connect_timeout=b.get("connect_timeout", TELEGRAM_DEFAULT_CONNECT_TIMEOUT),
                proxy=b.get("proxy", TELEGRAM_DEFAULT_PROXY),
            )
        )
    return configs
