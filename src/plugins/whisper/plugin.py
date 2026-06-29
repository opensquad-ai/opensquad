# -*- coding: utf-8 -*-
"""
Whisper Transcribe Tool Plugin (New-style Decorator API)

Tool implementation lives in plugins/whisper/whisper_transcribe.py.
Service auto-start: launches the Whisper Flask service on plugin load.
"""
import importlib
import logging
from typing import Any, Dict, List

from opensquad.plugin_api import register, Context
from opensquad.service_plugin import ServicePlugin

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
            "description": "Automatically start the Whisper service when the plugin loads",
        },
    },
)
class WhisperPlugin(ServicePlugin):
    """Whisper transcription tool plugin with auto-start service support."""

    def __init__(self, context: Context):
        # Call ServicePlugin's initializer, configure service parameters
        super().__init__(
            context=context,
            service_script="service.py",     # Whisper uses Flask (service.py)
            health_endpoint="/health",
            service_name="WhisperPlugin",
            max_startup_wait=10,
            health_check_interval=60,
        )

    def get_tool_modules(self) -> List[Dict[str, Any]]:
        """
        Proxy pattern: return the existing tool module for ToolRegistry.
        
        This method is recognized by PluginManager for new-style plugins
        that proxy existing tool modules instead of using @tool decorators.
        """
        tools = []
        try:
            module = importlib.import_module("plugins.whisper.whisper_transcribe")
            tools.append({
                "name": "whisper_transcribe",
                "module": module,
                "level": "core",
                "auto_register": False,
                "requires_agent_id": False,
            })
        except ImportError as e:
            logger.error(f"[WhisperPlugin] Cannot import whisper_transcribe module: {e}")
        return tools
