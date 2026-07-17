"""StepFun / OpenSquad audio helpers."""

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
    """Resolve voice.asr_card / voice.tts_card / voice.realtime_card from agent config."""
    voice = (agent_config or {}).get("voice") or {}
    key = f"{kind}_card"
    card_name = voice.get(key) or ""
    if not card_name:
        return None
    try:
        return load_model_card(card_name)
    except Exception as e:
        logger.warning("[audio] Failed to load voice.%s=%s: %s", key, card_name, e)
        return None


def http_base_url(card: dict[str, Any]) -> str:
    base = (card.get("base_url") or "https://api.stepfun.com/v1").rstrip("/")
    return base


def ws_realtime_url(card: dict[str, Any]) -> str:
    """Build StepFun realtime websocket URL from card base_url + model_name."""
    model = card.get("model_name") or "stepaudio-2.5-realtime"
    base = http_base_url(card)
    # https://api.stepfun.com/v1 -> wss://api.stepfun.com/v1/realtime
    if base.startswith("https://"):
        ws = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        ws = "ws://" + base[len("http://") :]
    else:
        ws = base
    if not ws.endswith("/realtime"):
        ws = ws.rstrip("/") + "/realtime"
    return f"{ws}?model={model}"
