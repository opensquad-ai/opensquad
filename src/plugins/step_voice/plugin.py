"""
ASR/TTS Translate plugin — cloud ASR transcription + TTS synthesis via model cards.

Provider is whatever the agent's voice.*_card (or inline voice config) points to;
not limited to StepFun.
"""

from __future__ import annotations

import logging
from typing import Any

from opensquad.plugin_api import Context, Plugin, register
from plugins.proxy_tools import proxy_tool_module

logger = logging.getLogger("plugins.asr_tts")


@register(
    name="asr_tts",
    author="OpenSquad",
    description=(
        "Cloud ASR/TTS tools. Transcribe audio and synthesize speech using the "
        "agent's voice model-card configuration (any OpenAI-compatible provider)."
    ),
    version="1.0.0",
    plugin_type="tool",
    display_name="ASR/TTS Translate",
    tags=["audio", "asr", "tts"],
)
class AsrTtsPlugin(Plugin):
    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self) -> None:
        logger.info("[AsrTtsPlugin] loaded.")

    def get_tool_modules(self) -> list[dict[str, Any]]:
        asr = proxy_tool_module(
            "plugins.step_voice.step_voice_tools",
            name="asr_tts",
            level="core",
            auto_register=True,
        )
        return [
            asr,
            proxy_tool_module(
                "plugins.step_voice.step_voice_tools",
                name="step_voice",
                level="core",
                auto_register=True,
                module=asr["module"],
            ),
        ]
