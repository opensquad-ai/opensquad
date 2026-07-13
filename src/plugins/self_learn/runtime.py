"""Resolve ChatAPI cfg + ToolRegistry for Self-Learn sub-agents.

UI actions run in the Launcher process (no agent runtime). Learning must run
inside the agent process. Prefer delegate init, fall back to the active runner.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger("plugins.self_learn.runtime")

_lock = threading.RLock()
_chat_api_cfg: dict[str, Any] | None = None
_tool_registry: Any = None


def set_runtime(chat_api_cfg: dict[str, Any] | None, tool_registry: Any = None) -> None:
    """Optional explicit injection from the self_learn plugin on_load."""
    global _chat_api_cfg, _tool_registry
    with _lock:
        if chat_api_cfg is not None:
            _chat_api_cfg = dict(chat_api_cfg)
        if tool_registry is not None:
            _tool_registry = tool_registry


def _cfg_from_chat_api(chat_api: Any, tool_registry: Any) -> dict[str, Any]:
    provider = "openai"
    try:
        cls = type(chat_api).__name__.lower()
        if "claude" in cls:
            provider = "claude"
        elif "google" in cls or "gemini" in cls:
            provider = "google"
    except Exception:
        pass

    prompt = ""
    try:
        prompt = getattr(chat_api, "_prompt_template", None) or ""
        if not prompt:
            pm = getattr(chat_api, "prompt_message", None) or {}
            prompt = str(pm.get("content") or "")
    except Exception:
        prompt = ""

    return {
        "provider": provider,
        "api_protocol": provider,
        "api_key": getattr(chat_api, "api_key", "") or "",
        "base_url": getattr(chat_api, "base_url", "") or "",
        "model": getattr(chat_api, "model", "") or "",
        "model_name": getattr(chat_api, "model", "") or "",
        "token_max": getattr(getattr(chat_api, "config", None), "token_max", None)
        or getattr(chat_api, "token_max", 32000),
        "temperature": getattr(chat_api, "temperature", 0.3),
        "timeout": getattr(chat_api, "timeout", 60.0),
        "is_img_model": getattr(chat_api, "is_img_model", False),
        "is_audio_model": getattr(chat_api, "is_audio_model", False),
        "is_video_model": getattr(chat_api, "is_video_model", False),
        "use_file_api": getattr(chat_api, "use_file_api", False),
        "file_api_size_threshold": getattr(chat_api, "file_api_size_threshold", 4 * 1024 * 1024),
        "is_think": getattr(chat_api, "is_think", False),
        "reasoning_effort": getattr(chat_api, "reasoning_effort", "high"),
        "parent_prompt": prompt,
        "prompt": prompt,
    }


def resolve_runtime() -> tuple[dict[str, Any] | None, Any, str]:
    """
    Returns (chat_api_cfg, tool_registry, source).
    source is one of: self_learn / delegate / active_runner / missing
    """
    with _lock:
        if _chat_api_cfg and _tool_registry is not None:
            return dict(_chat_api_cfg), _tool_registry, "self_learn"

    # Prefer delegate module (initialized at agent boot).
    try:
        from opensquad.tools import delegate as delegate_mod

        cfg = delegate_mod.get_chat_api_cfg()
        registry = getattr(delegate_mod, "_tool_registry", None)
        if cfg and registry is not None:
            return cfg, registry, "delegate"
    except Exception:
        logger.debug("[self_learn.runtime] delegate lookup failed", exc_info=True)

    # Fall back to the live AgentRunner singleton in this process.
    try:
        from opensquad import runner as runner_mod

        active = getattr(runner_mod, "_active_runner", None)
        if active is not None and getattr(active, "chat_api", None) is not None:
            registry = getattr(active, "tool_registry", None)
            if registry is not None:
                cfg = _cfg_from_chat_api(active.chat_api, registry)
                set_runtime(cfg, registry)
                return dict(cfg), registry, "active_runner"
    except Exception:
        logger.debug("[self_learn.runtime] active_runner lookup failed", exc_info=True)

    return None, None, "missing"
