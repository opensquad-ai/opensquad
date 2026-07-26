"""
Bocha Search Tool Plugin

Calls Bocha Web Search / AI Search HTTP APIs (no browser scraping).
Tool implementation: plugins/bocha_search/bocha_search.py
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from opensquad.plugin_api import Context, Plugin, register

logger = logging.getLogger("plugins.bocha_search")


@register(
    name="bocha_search",
    author="OpenSquad",
    description=(
        "Bocha (博查) web search API for AI. Returns high-quality webpage titles, "
        "URLs, summaries, site names, and publish dates — no browser scraping."
    ),
    version="1.0.0",
    plugin_type="tool",
    display_name="Bocha Search",
    dependencies={"pip": ["requests"]},
    tags=["search"],
    config_schema={
        "api_key": {
            "type": "string",
            "default": "",
            "description": "Bocha API key (or set BOCHA_API_KEY env).",
        },
        "base_url": {
            "type": "string",
            "default": "https://api.bocha.cn",
            "description": "Bocha API base URL",
        },
        "default_count": {
            "type": "integer",
            "default": 10,
            "description": "Default number of results (1-50)",
        },
        "timeout_sec": {
            "type": "integer",
            "default": 30,
            "description": "HTTP request timeout in seconds",
        },
    },
)
class BochaSearchPlugin(Plugin):
    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        logger.info("[BochaSearchPlugin] loaded.")

    def get_tool_modules(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        try:
            module = importlib.import_module("plugins.bocha_search.bocha_search")
            tools.append(
                {
                    "name": "bocha_search",
                    "module": module,
                    "level": "core",
                    "auto_register": True,
                    "requires_agent_id": False,
                }
            )
        except ImportError as e:
            logger.error("[BochaSearchPlugin] Cannot import bocha_search module: %s", e)
        return tools
