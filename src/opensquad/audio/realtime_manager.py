"""Process-level RealtimeSessionBridge holder for gateway_adapter commands."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_bridge = None
_runner = None


def bind_runner(runner) -> None:
    global _runner
    _runner = runner


def get_bridge():
    return _bridge


async def start_session(*, voice: str = "", instructions: str = "") -> dict[str, Any]:
    global _bridge
    from opensquad import agent_runtime_context as arc
    from opensquad.audio import resolve_voice_card
    from opensquad.audio.realtime_bridge import RealtimeSessionBridge
    from opensquad.events import bus

    if _bridge is not None:
        await stop_session()

    card = resolve_voice_card(arc.agent_config, "realtime")
    if not card:
        return {"ok": False, "error": "voice.realtime_card not configured"}

    voice_cfg = (arc.agent_config or {}).get("voice") or {}
    voice = voice or voice_cfg.get("realtime_voice") or ""
    instructions = instructions or voice_cfg.get("realtime_instructions") or ""
    if not instructions and _runner is not None:
        try:
            # Prefer system prompt from chat_api if available
            req = getattr(_runner.chat_api, "req", None) or []
            if req and isinstance(req[0], dict) and req[0].get("role") == "system":
                instructions = str(req[0].get("content") or "")[:4000]
        except Exception:
            pass
    if not instructions:
        instructions = "You are a helpful OpenSquad voice assistant."

    tool_registry = getattr(_runner, "tool_registry", None) if _runner else None

    async def _emit(event_type: str, data: Any) -> None:
        await bus.emit_async(event_type, data)

    bridge = RealtimeSessionBridge(
        card=card,
        tool_registry=tool_registry,
        instructions=instructions,
        voice=voice,
        emit=_emit,
        tool_filter="high",
    )
    await bridge.start()
    _bridge = bridge
    return {"ok": True, "status": "connected"}


async def stop_session() -> dict[str, Any]:
    global _bridge
    if _bridge is None:
        return {"ok": True, "status": "disconnected"}
    try:
        await _bridge.stop()
    finally:
        _bridge = None
    return {"ok": True, "status": "disconnected"}


async def append_audio(pcm16_b64: str) -> None:
    if _bridge is not None:
        await _bridge.append_audio(pcm16_b64)


async def commit_audio() -> None:
    if _bridge is not None:
        await _bridge.commit_audio()
