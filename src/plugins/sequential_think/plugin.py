# -*- coding: utf-8 -*-
"""
Sequential Think Tool Plugin (New-style Decorator API)

Tool implementation lives in plugins/sequential_think/sequential_think.py
with its dependency subpackage plugins/sequential_think/sequential_thinking/.
"""
import importlib
import logging
from typing import Any, Dict, List

from opensquad.plugin_api import register, Plugin, Context

logger = logging.getLogger("plugins.sequential_think")


@register(
    name="sequential_think",
    author="OpenSquad",
    description="Sequential thinking and reasoning tool. Provides structured thought processing, summary generation, and session management.",
    version="1.0.0",
    plugin_type="tool",
    display_name="Sequential Thinking",
    tags=["reasoning"],
)
class SequentialThinkPlugin(Plugin):
    """Sequential thinking tool plugin (proxy to opensquad.tools.sequential_think)."""

    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        logger.info("[SequentialThinkPlugin] loaded (new-style).")

    def get_tool_modules(self) -> List[Dict[str, Any]]:
        tools = []
        try:
            module = importlib.import_module("plugins.sequential_think.sequential_think")
            tools.append({
                "name": "sequential_think",
                "module": module,
                "level": "extended",
                "auto_register": False,
                "requires_agent_id": False,
            })
        except ImportError as e:
            logger.error(f"[SequentialThinkPlugin] Cannot import sequential_think module: {e}")
        return tools
