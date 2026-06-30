"""
Vision Tool Plugin (New-style Decorator API)

Tool implementation lives in plugins/vision/vision.py.
"""

import importlib
import logging
from typing import Any

from opensquad.plugin_api import Context, Plugin, register

logger = logging.getLogger("plugins.vision")


@register(
    name="vision",
    author="OpenSquad",
    description="Image reading tool. Writes image paths to img_path.txt for the vision model to process.",
    version="1.0.0",
    plugin_type="tool",
    display_name="Vision (Image Reader)",
    tags=["vision"],
)
class VisionPlugin(Plugin):
    """Vision tool plugin (proxy to opensquad.tools.vision)."""

    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        logger.info("[VisionPlugin] loaded (new-style).")

    def get_tool_modules(self) -> list[dict[str, Any]]:
        tools = []
        try:
            module = importlib.import_module("plugins.vision.vision")
            tools.append(
                {
                    "name": "vision",
                    "module": module,
                    "level": "extended",
                    "auto_register": True,
                    "requires_agent_id": False,
                }
            )
        except ImportError as e:
            logger.error(f"[VisionPlugin] Cannot import vision module: {e}")
        return tools
