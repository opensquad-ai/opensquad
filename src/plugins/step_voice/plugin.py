"""
Step Voice plugin — StepFun ASR + TTS tools (preferred over local Whisper).
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from opensquad.plugin_api import Context, Plugin, register

logger = logging.getLogger("plugins.step_voice")


@register(
    name="step_voice",
    author="OpenSquad",
    description="StepFun cloud ASR/TTS tools. Transcribe audio and synthesize speech via model cards.",
    version="1.0.0",
    plugin_type="tool",
    display_name="Step Voice (ASR/TTS)",
    tags=["audio", "asr", "tts"],
)
class StepVoicePlugin(Plugin):
    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        logger.info("[StepVoicePlugin] loaded.")

    def get_tool_modules(self) -> list[dict[str, Any]]:
        tools = []
        try:
            module = importlib.import_module("plugins.step_voice.step_voice_tools")
            tools.append(
                {
                    "name": "step_voice",
                    "module": module,
                    "level": "core",
                    "auto_register": True,
                    "requires_agent_id": False,
                }
            )
        except ImportError as e:
            logger.error("[StepVoicePlugin] Cannot import step_voice_tools: %s", e)
        return tools
