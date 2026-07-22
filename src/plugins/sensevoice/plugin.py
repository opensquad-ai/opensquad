"""
SenseVoice ASR Plugin (New-style Decorator API)

Local SenseVoice-Small INT8 ONNX speech-to-text service.
Model is NOT bundled — user downloads via plugin UI on first use.
Service lifecycle is owned by the Launcher PluginServiceProcess.
"""

import logging
from typing import Any

from opensquad.plugin_api import Context, Plugin, register

logger = logging.getLogger("plugins.sensevoice")


@register(
    name="sensevoice",
    author="OpenSquad",
    description=(
        "Local SenseVoice-Small INT8 ONNX speech-to-text. "
        "Download the model from the plugin panel before starting the service."
    ),
    version="1.0.0",
    plugin_type="tool",
    display_name="SenseVoice ASR",
    dependencies={
        "pip": [
            "onnxruntime",
            "soundfile",
            "librosa",
            "numpy",
            "pyyaml",
            "flask",
            "flask-cors",
        ]
    },
    tags=["audio", "asr"],
    config_schema={
        "port": {
            "type": "integer",
            "default": 7101,
            "description": "SenseVoice service port",
        },
        "host": {
            "type": "string",
            "default": "0.0.0.0",
            "description": "SenseVoice service listen address",
        },
        "auto_start": {
            "type": "boolean",
            "default": False,
            "description": "Automatically start SenseVoice when the launcher boots (requires model downloaded)",
        },
    },
)
class SenseVoicePlugin(Plugin):
    """SenseVoice ASR plugin. Service is managed by the Launcher."""

    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        logger.info("[SenseVoicePlugin] loaded (model is optional until downloaded).")

    def get_tool_modules(self) -> list[dict[str, Any]]:
        # No agent tool required for v1 — Agent Web uses the builtin model card.
        return []
