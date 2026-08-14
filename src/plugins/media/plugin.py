"""
Media Tool Plugin (New-style Decorator API)

Tool implementation lives in plugins/media/media.py.
"""

import logging
from typing import Any

from opensquad.plugin_api import Context, Plugin, register
from plugins.proxy_tools import proxy_tool_module

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
        return [
            proxy_tool_module(
                "plugins.media.media",
                name="media",
                level="core",
            )
        ]
