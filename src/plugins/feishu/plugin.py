"""
Feishu Platform Plugin (New-style Decorator API)

Provides Feishu/Lark outbound send tools.
Inbound adapter (adapter.py) remains as an independent process.
"""

import logging
from typing import Any

from opensquad.plugin_api import Context, Plugin, register
from plugins.proxy_tools import proxy_tool_module

logger = logging.getLogger("plugins.feishu")


@register(
    name="feishu",
    author="OpenSquad",
    description="Feishu/Lark platform integration. Provides inbound message adapter and outbound send tools for Feishu groups and users.",
    version="1.0.0",
    plugin_type="platform",
    display_name="Feishu (Lark)",
    config_section="feishu",
    dependencies={"pip": ["lark-oapi"]},
    tags=["im"],
    config_schema={
        "service_enabled": {
            "type": "boolean",
            "default": False,
            "description": "Enable Feishu service",
        },
        "bots": {
            "type": "bot_list",
            "default": [],
            "description": "Feishu bot list",
            "item_schema": {
                "name": {
                    "type": "string",
                    "default": "",
                    "description": "Bot name",
                },
                "app_id": {
                    "type": "string",
                    "default": "",
                    "description": "App ID",
                },
                "app_secret": {
                    "type": "string",
                    "default": "",
                    "description": "App Secret",
                    "secret": True,
                },
                "encrypt_key": {
                    "type": "string",
                    "default": "",
                    "description": "Encryption key (optional)",
                    "secret": True,
                },
                "verification_token": {
                    "type": "string",
                    "default": "",
                    "description": "Verification token (optional)",
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
class FeishuPlugin(Plugin):
    """Feishu/Lark platform plugin for OpenSquad."""

    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        logger.info("[FeishuPlugin] Feishu plugin loaded (new-style).")

    def get_tool_modules(self) -> list[dict[str, Any]]:
        return [
            proxy_tool_module(
                "plugins.feishu.send_tools",
                name="feishu_send",
                level="extended",
                auto_register=True,
                requires_agent_id=True,
            )
        ]

    def get_adapter_process_cmd(self):
        return ["python", "-m", "plugins.feishu.adapter"]
