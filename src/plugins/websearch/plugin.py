"""
WebSearch Tool Plugin (New-style Decorator API)

Tool implementation lives in plugins/websearch/websearch.py.
Service auto-start: launches the WebSearch FastAPI service on plugin load.
"""

import importlib
import logging
from typing import Any

from opensquad.plugin_api import Context, register
from opensquad.service_plugin import ServicePlugin

logger = logging.getLogger("plugins.websearch")


@register(
    name="websearch",
    author="OpenSquad",
    description="Web search and page fetching tools. Provides search, fetch, and fetch_html functions via a deployed WebSearch API service.",
    version="1.0.0",
    plugin_type="tool",
    display_name="Web Search",
    dependencies={
        "pip": [
            "requests",
            "fastapi",
            "uvicorn",
            "httpx",
            "beautifulsoup4",
            "lxml",
            "tiktoken",
            "playwright",
            "playwright-stealth",
            "trafilatura",
            "PyMuPDF",
        ]
    },
    tags=["search"],
    config_schema={
        "port": {
            "type": "integer",
            "default": 9001,
            "description": "WebSearch service port",
        },
        "host": {
            "type": "string",
            "default": "0.0.0.0",
            "description": "WebSearch service listen address",
        },
        "auto_start": {
            "type": "boolean",
            "default": True,
            "description": "Auto-start the WebSearch service when the plugin loads",
        },
    },
)
class WebSearchPlugin(ServicePlugin):
    """WebSearch tool plugin with auto-start service support."""

    def __init__(self, context: Context):
        # Call ServicePlugin initialization with service configuration parameters
        super().__init__(
            context=context,
            service_script="main.py",  # WebSearch uses FastAPI (main.py)
            health_endpoint="/health",
            service_name="WebSearchPlugin",
            max_startup_wait=10,
            health_check_interval=60,
        )

    def on_unload(self) -> None:
        """Override: stop service + shutdown browser singleton."""
        super().on_unload()
        try:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()
            if loop and not loop.is_closed():
                from .service.websearch_api import shutdown_browser

                loop.run_until_complete(shutdown_browser())
        except Exception as e:
            logger.warning(f"[WebSearchPlugin] Browser shutdown error: {e}")

    def get_tool_modules(self) -> list[dict[str, Any]]:
        """
        Proxy pattern: return the existing tool module for ToolRegistry.

        This method is recognized by PluginManager for new-style plugins
        that proxy existing tool modules instead of using @tool decorators.
        """
        tools = []
        try:
            module = importlib.import_module("plugins.websearch.websearch")
            tools.append(
                {
                    "name": "websearch",
                    "module": module,
                    "level": "core",
                    "auto_register": True,
                    "requires_agent_id": False,
                }
            )
        except ImportError as e:
            logger.error(f"[WebSearchPlugin] Cannot import websearch module: {e}")
        return tools
