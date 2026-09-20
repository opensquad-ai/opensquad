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


# Built-in local ASR services, most-preferred first.  SenseVoice leads because
# the installer ships it pre-enabled; Whisper is the fallback.
_BUILTIN_ASR_SERVICES: tuple[str, ...] = ("sensevoice", "whisper")


def builtin_asr_service_of(card: dict[str, Any] | None) -> str:
    """Return the built-in ASR service a card backs (``sensevoice``/``whisper``), else ``""``."""
    if not card:
        return ""
    svc = (card.get("builtin_service") or "").strip().lower()
    return svc if svc in _BUILTIN_ASR_SERVICES else ""


def _iter_card_names() -> list[str]:
    """Model-card names visible to the app, workspace first (dedup, stable order)."""
    seen: set[str] = set()
    names: list[str] = []
    for cards_dir in syscfg.resource_search_dirs("model_cards"):
        if not os.path.isdir(cards_dir):
            continue
        try:
            entries = sorted(os.listdir(cards_dir))
        except OSError:
            continue
        for fname in entries:
            if not fname.endswith(".json"):
                continue
            name = fname[:-5]
            if name in seen:
                continue
            seen.add(name)
            names.append(name)
    return names


def _resolve_builtin_asr_card() -> dict[str, Any] | None:
    """Best built-in local ASR card (``builtin_service`` = sensevoice|whisper)."""
    best: dict[str, Any] | None = None
    best_key: tuple[int, int, str] | None = None
    for name in _iter_card_names():
        try:
            card = load_model_card(name)
        except Exception:
            continue
        svc = builtin_asr_service_of(card)
        if not svc:
            continue
        try:
            enabled = bool(syscfg.is_service_enabled(svc))
        except Exception:
            enabled = False
        # enabled first, then fixed service preference, then name for determinism
        key = (0 if enabled else 1, _BUILTIN_ASR_SERVICES.index(svc), name)
        if best_key is None or key < best_key:
            best, best_key = card, key
    return best


def _resolve_flagged_group_asr_card() -> dict[str, Any] | None:
    """Legacy fallback: a workspace card explicitly marked ``group_asr: true``."""
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


def resolve_group_asr_card() -> dict[str, Any] | None:
    """Resolve the ASR used for group-chat voice input (speech-to-text).

    Group chat has no per-agent voice config to fall back on, so its voice input
    is a **built-in** capability: it must use the locally hosted built-in ASR
    service (SenseVoice / Whisper) rather than a user's cloud model card.

    A cloud card is the wrong default here — it can be unreachable, rate-limited
    or, as happened with ``stepaudio-2.5-asr`` (StepFun returns
    ``404 model_invalid``), simply not provisioned for the account, which
    surfaces to every group member as a broken mic button.

    Resolution order:
      1. the best **built-in** ASR card (``builtin_service`` = sensevoice|whisper),
         preferring a service enabled in ``system_config.json``;
      2. only if no built-in ASR card exists at all, a card explicitly marked
         ``group_asr: true`` (kept so installs without a local ASR still work).

    Returns ``None`` when neither is available; the caller should then tell the
    user to enable the built-in ASR service.
    """
    builtin = _resolve_builtin_asr_card()
    if builtin is not None:
        logger.debug(
            "[audio] group ASR -> built-in card %s (service=%s)",
            builtin.get("_card"),
            builtin_asr_service_of(builtin),
        )
        return builtin
    legacy = _resolve_flagged_group_asr_card()
    if legacy is not None:
        logger.warning(
            "[audio] no built-in ASR card available; falling back to model card %s flagged group_asr=true",
            legacy.get("_card"),
        )
    return legacy


def http_base_url(card: dict[str, Any]) -> str:
    """Return the card's HTTP API base URL (no provider-specific default)."""
    return (card.get("base_url") or "").strip().rstrip("/")


def resolve_asr_base_url(card: dict[str, Any]) -> str:
    """Resolve ASR HTTP base URL, rewriting builtin local ASR services.

    Builtin cards set ``builtin_service`` (e.g. ``whisper`` / ``sensevoice``).
    Their placeholder ``base_url`` is ignored; we use the live plugin service
    URL + ``/v1`` so the OpenAI-compatible client hits
    ``POST …/v1/audio/transcriptions``.
    """
    builtin = (card.get("builtin_service") or "").strip().lower()
    if builtin in ("whisper", "sensevoice"):
        if builtin == "whisper":
            root = (syscfg.whisper_url() or "").strip().rstrip("/")
        else:
            root = (syscfg.sensevoice_url() or "").strip().rstrip("/")
        if not root:
            return http_base_url(card)
        if root.endswith("/v1"):
            return root
        return f"{root}/v1"
    return http_base_url(card)


def asr_protocol_of(card: dict[str, Any] | None) -> str:
    """Return ASR wire protocol for a voice card (default: stepfun_sse)."""
    if not card:
        return "stepfun_sse"
    proto = (card.get("asr_protocol") or "").strip().lower()
    if proto in ("openai_transcriptions", "openai", "whisper", "sensevoice"):
        return "openai_transcriptions"
    if proto in ("stepfun_sse", "stepfun"):
        return "stepfun_sse"
    if proto:
        return proto
    # Omitted asr_protocol: builtin local ASR → OpenAI transcriptions; else StepFun SSE.
    if (card.get("builtin_service") or "").strip().lower() in ("whisper", "sensevoice"):
        return "openai_transcriptions"
    return "stepfun_sse"


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
