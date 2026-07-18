"""OpenSquad audio helpers (TTS OpenAI-compat; ASR/Realtime may be provider-specific)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from opensquad.system_config import syscfg

logger = logging.getLogger(__name__)


def load_model_card(card_name: str) -> dict[str, Any]:
    """Load a model card JSON from the workspace model_cards directory."""
    if not card_name or not isinstance(card_name, str):
        raise ValueError("card name is required")
    safe = os.path.basename(card_name.strip())
    if safe != card_name.strip():
        raise ValueError(f"invalid card name: {card_name!r}")
    path = os.path.join(syscfg.workspace_model_cards_dir(), f"{safe}.json")
    if not os.path.isfile(path):
        # Fallback to install/src model_cards for templates
        install = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "model_cards", f"{safe}.json")
        install = os.path.abspath(install)
        if os.path.isfile(install):
            path = install
        else:
            raise FileNotFoundError(f"model card not found: {safe}")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["_card"] = safe
    return cfg


def resolve_voice_card(agent_config: dict[str, Any] | None, kind: str) -> dict[str, Any] | None:
    """Resolve ASR / TTS / Realtime credentials for an agent.

    Priority:
      1. voice.{kind}_card → load model card JSON
      2. voice.base_url + api_key + {kind}_model → synthesize inline card dict
    """
    voice = (agent_config or {}).get("voice") or {}
    if not isinstance(voice, dict):
        return None

    key = f"{kind}_card"
    card_name = (voice.get(key) or "").strip()
    if card_name:
        try:
            return load_model_card(card_name)
        except Exception as e:
            logger.warning("[audio] Failed to load voice.%s=%s: %s", key, card_name, e)
            return None

    model_key = f"{kind}_model"
    model_name = (voice.get(model_key) or "").strip()
    api_key = (voice.get("api_key") or "").strip()
    base_url = (voice.get("base_url") or "").strip()
    if not model_name or not api_key or not base_url:
        return None

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model_name": model_name,
        "audio_output_voice": (voice.get("realtime_voice") or "").strip(),
        "provider": voice.get("provider") or "inline",
        "api_protocol": voice.get("api_protocol") or "openai_compat",
        "_card": f"inline-{kind}",
    }


async def auto_transcribe_audio_paths(
    agent_config: dict[str, Any] | None,
    audio_paths: list[str],
    *,
    language: str = "zh",
) -> str | None:
    """If ASR is configured, transcribe paths and return text.

    Returns None when ASR is unavailable (caller should keep Tip).
    Returns empty string when enabled but all transcripts were empty.
    """
    if not audio_paths:
        return None
    card = resolve_voice_card(agent_config, "asr")
    if not card:
        return None

    from opensquad.audio.stepfun_asr import transcribe_with_card

    parts: list[str] = []
    for path in audio_paths:
        try:
            result = await transcribe_with_card(card, path, language=language)
        except Exception as e:
            logger.warning("[audio] auto_asr failed for %s: %s", path, e)
            continue
        if not result.get("success"):
            logger.warning("[audio] auto_asr error for %s: %s", path, result.get("error"))
            continue
        text = (result.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def resolve_group_asr_card() -> dict[str, Any] | None:
    """Load the workspace model card marked ``group_asr: true`` (ASR for group chat)."""
    cards_dir = syscfg.workspace_model_cards_dir()
    if not os.path.isdir(cards_dir):
        return None

    try:
        names = sorted(os.listdir(cards_dir))
    except OSError:
        return None

    for fname in names:
        if not fname.endswith(".json"):
            continue
        name = fname[:-5]
        try:
            card = load_model_card(name)
        except Exception:
            continue
        if card.get("group_asr"):
            return card
    return None


def http_base_url(card: dict[str, Any]) -> str:
    """Return the card's HTTP API base URL (no provider-specific default)."""
    return (card.get("base_url") or "").strip().rstrip("/")


def ws_realtime_url(card: dict[str, Any]) -> str:
    """Build realtime websocket URL from card base_url + model_name.

    Examples:
      https://api.stepfun.com/step_plan/v1
        -> wss://api.stepfun.com/step_plan/v1/realtime?model=...
      https://api.stepfun.com/v1
        -> wss://api.stepfun.com/v1/realtime?model=...
    """
    model = card.get("model_name") or "stepaudio-2.5-realtime"
    base = http_base_url(card)
    if base.startswith("https://"):
        ws = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        ws = "ws://" + base[len("http://") :]
    else:
        ws = base
    if not ws.endswith("/realtime"):
        ws = ws.rstrip("/") + "/realtime"
    return f"{ws}?model={model}"
