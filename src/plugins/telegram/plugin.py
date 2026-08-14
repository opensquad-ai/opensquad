"""
Telegram Platform Plugin (New-style Decorator API)

Provides Telegram outbound send tools.
Inbound adapter (adapter.py) remains as an independent process.
"""

import logging
from typing import Any

from opensquad.plugin_api import Context, Plugin, register
from plugins.proxy_tools import proxy_tool_module

logger = logging.getLogger("plugins.telegram")


@register(
    name="telegram",
    author="OpenSquad",
    description="Telegram platform integration. Provides inbound message adapter and outbound send tools for Telegram chats.",
    version="1.0.0",
    plugin_type="platform",
    display_name="Telegram",
    dependencies={"pip": ["requests", "python-telegram-bot"]},
    tags=["im"],
    config_schema={
        "service_enabled": {
            "type": "boolean",
            "default": False,
            "description": "Enable Telegram service",
        },
        "proxy": {
            "type": "string",
            "default": "",
            "description": "HTTP/SOCKS proxy for Telegram API (e.g. http://127.0.0.1:7890)",
        },
        "connect_timeout": {
            "type": "integer",
            "default": 30,
            "description": "Connection timeout in seconds",
        },
        "request_timeout": {
            "type": "integer",
            "default": 60,
            "description": "Request timeout in seconds",
        },
        "bots": {
            "type": "bot_list",
            "default": [],
            "description": "Telegram bot list",
            "item_schema": {
                "name": {
                    "type": "string",
                    "default": "",
                    "description": "Bot name",
                },
                "bot_token": {
                    "type": "string",
                    "default": "",
                    "description": "Bot Token",
                    "secret": True,
                },
                "agent_id": {
                    "type": "string",
                    "default": "",
                    "description": "Bound Agent ID",
                },
                "enabled": {
                    "type": "boolean",
                    "default": True,
                    "description": "Enable this bot",
                },
            },
        },
    },
)
class TelegramPlugin(Plugin):
    """Telegram platform plugin for OpenSquad."""

    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        logger.info("[TelegramPlugin] Telegram plugin loaded (new-style).")

    def get_tool_modules(self) -> list[dict[str, Any]]:
        return [
            proxy_tool_module(
                "plugins.telegram.send_tools",
                name="telegram_send",
                level="extended",
                auto_register=True,
                requires_agent_id=True,
            )
        ]

    def get_adapter_process_cmd(self):
        return ["python", "-m", "plugins.telegram.adapter"]
