"""
Whisper Transcribe Tool Plugin (New-style Decorator API)

Tool implementation lives in plugins/whisper/whisper_transcribe.py.
Service lifecycle is owned by the Launcher's PluginServiceProcess
(declared via `service` field in plugin.json).
"""

import importlib
import logging
from typing import Any

from opensquad.plugin_api import Context, Plugin, register

logger = logging.getLogger("plugins.whisper")


@register(
    name="whisper_transcribe",
    author="OpenSquad",
    description="Speech-to-text transcription tool using Whisper service. Supports Chinese and English audio transcription.",
    version="1.0.0",
    plugin_type="tool",
    display_name="Whisper Transcription",
    dependencies={"pip": ["requests", "whisper", "flask", "flask-cors"]},
    tags=["audio"],
    config_schema={
        "port": {
            "type": "integer",
            "default": 5001,
            "description": "Whisper service port",
        },
        "host": {
            "type": "string",
            "default": "0.0.0.0",
            "description": "Whisper service listen address",
        },
        "auto_start": {
            "type": "boolean",
            "default": True,
            "description": "Automatically start the Whisper service when the launcher boots",
        },
    },
)
class WhisperPlugin(Plugin):
    """Whisper transcription tool plugin. Service is managed by the Launcher."""

    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        logger.info("[WhisperPlugin] loaded.")

    def get_tool_modules(self) -> list[dict[str, Any]]:
        """
        Proxy pattern: return the existing tool module for ToolRegistry.

        This method is recognized by PluginManager for new-style plugins
        that proxy existing tool modules instead of using @tool decorators.
        """
        tools = []
        try:
            module = importlib.import_module("plugins.whisper.whisper_transcribe")
            tools.append(
                {
                    "name": "whisper_transcribe",
                    "module": module,
                    "level": "core",
                    "auto_register": False,
                    "requires_agent_id": False,
                }
            )
        except ImportError as e:
            logger.error(f"[WhisperPlugin] Cannot import whisper_transcribe module: {e}")
        return tools
