"""
External API Platform Plugin (New-style Decorator API)

Provides HTTP/WebSocket gateway adapter.
No outbound tools -- this plugin is an inbound-only adapter.
"""

import logging
from typing import Any

from opensquad.plugin_api import Context, Plugin, register

logger = logging.getLogger("plugins.external_api")


@register(
    name="external_api",
    author="OpenSquad",
    description="External API adapter. Provides HTTP/WebSocket gateway for third-party system integrations.",
    version="1.0.0",
    plugin_type="platform",
    display_name="External API",
    dependencies={"pip": ["fastapi", "uvicorn", "websockets"]},
    tags=["platform"],
)
class ExternalApiPlugin(Plugin):
    """External API platform plugin for OpenSquad."""

    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        logger.info("[ExternalApiPlugin] External API plugin loaded (new-style).")

    def get_tool_modules(self) -> list[dict[str, Any]]:
        """No outbound tools for external_api."""
        return []

    def get_adapter_process_cmd(self):
        return ["python", "-m", "plugins.external_api.adapter"]
