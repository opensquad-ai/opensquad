"""
Media Tool Plugin (New-style Decorator API)

Tool implementation lives in plugins/media/media.py.
"""

import importlib
import logging
from typing import Any

from opensquad.plugin_api import Context, Plugin, register

logger = logging.getLogger("plugins.media")


@register(
    name="media",
    author="OpenSquad",
    description="Audio format conversion tool. Converts audio files (e.g. webm to wav/mp3) using ffmpeg.",
    version="1.0.0",
    plugin_type="tool",
    display_name="Media Tools",
    tags=["audio"],
    dependencies={"pip": ["pydub", "imageio-ffmpeg"]},
)
class MediaPlugin(Plugin):
    """Media tool plugin (proxy to opensquad.tools.media)."""

    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        logger.info("[MediaPlugin] loaded (new-style).")

    def get_tool_modules(self) -> list[dict[str, Any]]:
        tools = []
        try:
            module = importlib.import_module("plugins.media.media")
            tools.append(
                {
                    "name": "media",
                    "module": module,
                    "level": "core",
                    "auto_register": False,
                    "requires_agent_id": False,
                }
            )
        except ImportError as e:
            logger.error(f"[MediaPlugin] Cannot import media module: {e}")
        return tools
