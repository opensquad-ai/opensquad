"""
Step Voice tools: ASR transcription + TTS synthesis via StepFun model cards.

Agent config:
  "voice": { "asr_card": "stepaudio-2.5-asr", "tts_card": "stepaudio-2.5-tts" }
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("tool_step_voice")

# Injected at agent boot (optional). Falls back to reading agent config from cwd.
_AGENT_CONFIG: dict[str, Any] | None = None


def set_agent_config(config: dict[str, Any] | None) -> None:
    global _AGENT_CONFIG
    _AGENT_CONFIG = config


def _get_agent_config() -> dict[str, Any]:
    if isinstance(_AGENT_CONFIG, dict) and _AGENT_CONFIG:
        return _AGENT_CONFIG
    # Best-effort: Runner may stash config on a module attribute later
    try:
        from opensquad import agent_runtime_context as ctx

        cfg = getattr(ctx, "agent_config", None)
        if isinstance(cfg, dict):
            return cfg
    except Exception:
        pass
    return {}


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already in async loop — run in a dedicated thread to avoid nested run
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


def transcribe_audio_file(audio_path: str, language: str = "zh") -> dict[str, Any]:
    """
    Transcribe an audio file using StepFun ASR (voice.asr_card).

    Args:
        audio_path: Absolute path to the audio file
        language: Language code, default 'zh'

    Returns:
        {"success": True, "text": "..."} or {"success": False, "error": "..."}
    """
    if not audio_path or not os.path.exists(audio_path):
        return {"success": False, "error": f"File not found: {audio_path}"}

    from opensquad.audio import resolve_voice_card
    from opensquad.audio.stepfun_asr import transcribe_with_card

    card = resolve_voice_card(_get_agent_config(), "asr")
    if not card:
        return {
            "success": False,
            "error": "No voice.asr_card configured. Set agent voice.asr_card to a StepFun ASR model card.",
        }
    if not card.get("api_key") or card.get("api_key", "").startswith("YOUR_"):
        return {"success": False, "error": "ASR model card api_key is missing"}

    try:
        return _run_async(transcribe_with_card(card, audio_path, language=language or "zh"))
    except Exception as e:
        logger.error("[step_voice] ASR failed: %s", e)
        return {"success": False, "error": str(e)}


def synthesize_speech(
    text: str,
    voice: str = "",
    instruction: str = "",
) -> dict[str, Any]:
    """
    Synthesize speech with StepFun TTS (voice.tts_card).

    Only call this when you intentionally want to send a voice reply to the user.
    The runtime will attach the generated audio to the chat bubble via output_media.

    Args:
        text: Text to speak (may include parenthetical inline TTS instructions)
        voice: Optional voice id (defaults to card audio_output_voice)
        instruction: Optional global style instruction for stepaudio-2.5-tts

    Returns:
        {"success": True, "url": "/uploads/...", "path": "...", "__output_media__": [...]}
    """
    if not (text or "").strip():
        return {"success": False, "error": "text is required"}

    from opensquad.audio import resolve_voice_card
    from opensquad.audio.stepfun_tts import synthesize_with_card

    card = resolve_voice_card(_get_agent_config(), "tts")
    if not card:
        return {
            "success": False,
            "error": "No voice.tts_card configured. Set agent voice.tts_card to a StepFun TTS model card.",
        }
    if not card.get("api_key") or card.get("api_key", "").startswith("YOUR_"):
        return {"success": False, "error": "TTS model card api_key is missing"}

    try:
        return _run_async(synthesize_with_card(card, text=text, voice=voice or "", instruction=instruction or ""))
    except Exception as e:
        logger.error("[step_voice] TTS failed: %s", e)
        return {"success": False, "error": str(e)}
