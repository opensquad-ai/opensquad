"""
MCP Query Tool Plugin (New-style Decorator API)

Tool implementation lives in plugins/mcp_query/mcp_query.py.
mcp_query depends on opensquad.tools.mcp_adapter which remains in
opensquad/tools/ as it is also used by runner.py and boot.py.
"""

import logging
from typing import Any

from opensquad.plugin_api import Context, Plugin, register
from plugins.proxy_tools import proxy_tool_module

logger = logging.getLogger("plugins.mcp_query")


@register(
    name="mcp_query",
    author="OpenSquad",
    description="MCP server management and querying tools. Allows agents to list, add, remove, reconnect, and reload MCP servers at runtime.",
    version="1.0.0",
    plugin_type="tool",
    display_name="MCP Server Management",
    tags=["agent"],
)
class McpQueryPlugin(Plugin):
    """MCP query tool plugin (proxy to opensquad.tools.mcp_query)."""

    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        logger.info("[McpQueryPlugin] loaded (new-style).")

    def get_tool_modules(self) -> list[dict[str, Any]]:
        return [
            proxy_tool_module(
                "plugins.mcp_query.mcp_query",
                name="mcp_query",
                level="extended",
                auto_register=True,
            )
        ]
