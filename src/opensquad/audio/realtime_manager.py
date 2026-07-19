"""Process-level RealtimeSessionBridge holder for gateway_adapter commands."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_bridge = None
_runner = None
_ask_lock = asyncio.Lock()

# No registry tools in realtime — only ask_agent (local) → main Agent Web Runner.
_REALTIME_TOOL_NAMESPACES: list[str] = []

_VOICE_NO_REPLY = "[VOICE_NO_REPLY]"

# Tag wrapping live voice utterances pushed into Agent Web InputHub.
# Rules for this mode live in the agent system prompt (see agents_boot.build_system_prompt).
_REALTIME_VOICE_OPEN = "realtime_voice"

_VOICE_INSTRUCTIONS = """You are the voice front-desk for OpenSquad Agent Web (same agent as the chat UI).

HARD RULES:
1) For weather, news, search, facts you are unsure about, files, coding, plans, “帮我…”:
   you MUST call tool ask_agent with the user's full question. Do NOT answer those from your own knowledge.
2) Skip ask_agent for: greetings / thanks / goodbye, or current time/date (use [Session context]).
3) Never invent tool calls in speech. Never say “我去查一下” without actually calling ask_agent.
4) After ask_agent returns: if the result contains [VOICE_NO_REPLY], stay silent (do not speak).
   Otherwise speak a short answer in the user's language immediately.

ask_agent = the SAME main agent as Agent Web chat (full tools: websearch, filesystem, etc.)."""


def is_voice_no_reply(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _VOICE_NO_REPLY in t:
        return True
    compact = t.replace(" ", "")
    return compact in (_VOICE_NO_REPLY, "VOICE_NO_REPLY", "【语音无需回复】", "语音无需回复")


def sanitize_for_tts(text: str) -> str:
    """Strip symbols TTS tends to read aloud (emoji, markdown *, #, etc.)."""
    import re

    t = (text or "").strip()
    if not t:
        return t
    # Emoji / pictographs / dingbats
    t = re.sub(
        r"[\U0001F300-\U0001F9FF\U0001FA00-\U0001FAFF\U00002600-\U000026FF"
        r"\U00002700-\U000027BF\U0001F000-\U0001F02F\U0001F0A0-\U0001F0FF]+",
        "",
        t,
    )
    # Markdown-ish markers TTS often verbalizes
    t = t.replace("**", "").replace("__", "").replace("~~", "")
    t = t.replace("*", "").replace("`", "").replace("#", "")
    t = t.replace("•", "，").replace("★", "").replace("☆", "").replace("→", "，")
    t = t.replace("|", "，")
    # Collapse leftover whitespace
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def bind_runner(runner) -> None:
    global _runner
    _runner = runner


def get_bridge():
    return _bridge


def get_runner():
    return _runner


def get_session_status() -> dict[str, Any]:
    """Return whether a voice realtime/mouthpiece session is currently active."""
    if _bridge is None:
        return {"ok": True, "status": "idle", "active": False}
    force = bool(getattr(_bridge, "force_ask_agent", False))
    mode = getattr(_bridge, "mode", "realtime" if not force else "mouthpiece")
    return {
        "ok": True,
        "status": "connected",
        "active": True,
        "force_ask_agent": force,
        "mode": mode,
    }


def _unwrap_bus_payload(data: Any) -> str:
    """Runner._emit wraps as {sid, data}; content may be str or {text: ...}."""
    if isinstance(data, dict) and "sid" in data and "data" in data:
        data = data["data"]
    if isinstance(data, dict):
        for key in ("text", "content", "message", "reply"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return ""
    if isinstance(data, str):
        return data.strip()
    return str(data).strip() if data is not None else ""


async def ask_main_agent(
    question: str,
    *,
    timeout: float = 180.0,
    wait_reply: bool = True,
) -> str:
    """
    Push a question into the same agent InputHub used by Agent Web.

    When wait_reply=True, also wait for to_user_final / to_user_end_task.
    When wait_reply=False, only push (for mid-workflow barge-in / follow-up speech);
    the Runner merges these as supplements while tools are running.
    """
    q = (question or "").strip()
    if not q:
        return "Error: empty question"

    from opensquad.events import bus
    from opensquad.input_hub import get_input_hub

    hub = get_input_hub()
    req_id = uuid.uuid4().hex[:8]

    # Slim user turn: mode rules live in system prompt; message only carries the tag.
    prompt = f'<{_REALTIME_VOICE_OPEN} id="{req_id}">\n{q}\n</{_REALTIME_VOICE_OPEN}>'

    # Wake if sleeping (same as Agent Web chat path).
    try:
        from opensquad.sleep_controller import sleep_controller
        from opensquad.state_manager import state_manager

        ai_state = await state_manager.get_state()
        if ai_state == "sleeping":
            sleep_controller.wake_up("voice-ask")
            logger.warning("[ask_agent] woke agent from sleep req=%s", req_id)
    except Exception as wake_err:
        logger.debug("[ask_agent] wake check skipped: %s", wake_err)

    # ALWAYS push immediately so mid-workflow speech reaches the Runner as a supplement,
    # even while a previous ask_main_agent is still waiting for to_user_final.
    await bus.emit_async(
        "voice_realtime_status",
        {
            "status": "tool_running",
            "tool": "ask_agent",
            "phase": "delegated" if wait_reply else "supplement",
            "req": req_id,
            "listening": True,
        },
    )
    hub.push(prompt, source="voice", channel="voice", sender_name="Voice")
    logger.warning(
        "[ask_agent] pushed to input_hub req=%s wait=%s q=%s",
        req_id,
        wait_reply,
        q[:120],
    )

    if not wait_reply:
        return ""

    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()
    sub_ids: list[str] = []

    def _on_final(data: Any) -> None:
        if fut.done():
            return
        text = _unwrap_bus_payload(data)
        if not text:
            return
        logger.warning("[ask_agent] got runner reply req=%s len=%d", req_id, len(text))
        try:
            fut.set_result(text)
        except Exception:
            pass

    # Serialize waiters only — pushes already happened above without the lock.
    async with _ask_lock:
        # Prefer early to_user_reply so voice can speak mid-workflow (not only final).
        for evt in ("to_user_reply", "to_user_final", "to_user_end_task"):
            sub_ids.append(bus.subscribe(evt, _on_final, owner=f"voice_ask_{req_id}"))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            logger.error("[ask_agent] timed out after %.0fs req=%s", timeout, req_id)
            return (
                f"Error: the main agent did not finish within {int(timeout)}s. "
                "Ask the user to retry, or check Agent Web for the running task."
            )
        except Exception as e:
            logger.error("[ask_agent] failed req=%s: %s", req_id, e)
            return f"Error: ask_agent failed: {e}"
        finally:
            for sid in sub_ids:
                with _Suppress():
                    bus.unsubscribe_by_id(sid)


class _Suppress:
    """Tiny suppress helper without importing contextlib at module import in hot path."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return True


async def start_session(
    *,
    voice: str = "",
    instructions: str = "",
    force_ask_agent: bool | None = None,
) -> dict[str, Any]:
    global _bridge
    from datetime import datetime

    from opensquad import agent_runtime_context as arc
    from opensquad.audio import resolve_voice_card
    from opensquad.events import bus

    voice_cfg = (arc.agent_config or {}).get("voice") or {}
    if force_ask_agent is None:
        force_ask_agent = bool(voice_cfg.get("force_ask_agent", True))
    else:
        force_ask_agent = bool(force_ask_agent)

    # Resume / double-start: keep the existing session when mode matches
    # (page refresh reconnect must not tear down a live mouthpiece call).
    if _bridge is not None:
        current_force = bool(getattr(_bridge, "force_ask_agent", False))
        if bool(force_ask_agent) == current_force:
            st = get_session_status()
            st["resumed"] = True
            return st
        await stop_session()

    async def _emit(event_type: str, data: Any) -> None:
        await bus.emit_async(event_type, data)

    # Force / 纯嘴替：ASR 卡 → 主 Agent → TTS 卡（不连 Realtime WS）
    if force_ask_agent:
        from opensquad.audio.mouthpiece_bridge import MouthpieceSession

        asr_card = resolve_voice_card(arc.agent_config, "asr")
        tts_card = resolve_voice_card(arc.agent_config, "tts")
        if not asr_card:
            return {
                "ok": False,
                "status": "error",
                "error": "mouthpiece mode requires voice.asr_card or voice.asr_model (+ base_url/api_key)",
                "force_ask_agent": True,
                "mode": "mouthpiece",
            }
        if not tts_card:
            return {
                "ok": False,
                "status": "error",
                "error": "mouthpiece mode requires voice.tts_card or voice.tts_model (+ base_url/api_key)",
                "force_ask_agent": True,
                "mode": "mouthpiece",
            }
        voice = voice or voice_cfg.get("realtime_voice") or voice_cfg.get("tts_voice") or ""
        bridge = MouthpieceSession(
            asr_card=asr_card,
            tts_card=tts_card,
            voice=voice,
            emit=_emit,
            ask_agent=ask_main_agent,
        )
        await bridge.start()
        _bridge = bridge
        return {
            "ok": True,
            "status": "connected",
            "force_ask_agent": True,
            "mode": "mouthpiece",
        }

    # Non-force：StepFun Realtime 双工
    from opensquad.audio.realtime_bridge import RealtimeSessionBridge

    card = resolve_voice_card(arc.agent_config, "realtime")
    if not card:
        return {
            "ok": False,
            "status": "error",
            "error": "Realtime not configured — set voice.realtime_model (+ base_url/api_key) or voice.realtime_card",
        }

    voice = voice or voice_cfg.get("realtime_voice") or ""
    instructions = (instructions or voice_cfg.get("realtime_instructions") or "").strip()
    if not instructions:
        instructions = _VOICE_INSTRUCTIONS
    else:
        instructions = f"{_VOICE_INSTRUCTIONS}\n\n{instructions[:1500]}"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    instructions = (
        f"{instructions}\n\n[Session context] Local time now is {now}. "
        "If the user asks 几点了 / what time is it, speak this time immediately."
    )

    tool_registry = getattr(_runner, "tool_registry", None) if _runner else None

    bridge = RealtimeSessionBridge(
        card=card,
        tool_registry=tool_registry,
        instructions=instructions,
        voice=voice,
        emit=_emit,
        tool_filter=_REALTIME_TOOL_NAMESPACES,
        local_tool_handler=_local_tool_handler,
        force_ask_agent=False,
    )
    await bridge.start()
    _bridge = bridge
    return {"ok": True, "status": "connected", "force_ask_agent": False, "mode": "realtime"}


def set_session_options(*, force_ask_agent: bool | None = None) -> dict[str, Any]:
    """
    Update live options. Switching force on/off mid-call is not supported without
    reconnect — advise the UI to restart the session.
    """
    if _bridge is None:
        # Idle toggle must not surface as a connection error in the UI.
        return {
            "ok": True,
            "status": "idle",
            "force_ask_agent": bool(force_ask_agent) if force_ask_agent is not None else None,
            "note": "no active session; option applies on next voice_realtime_start",
        }
    current_force = bool(getattr(_bridge, "force_ask_agent", False))
    mode = getattr(_bridge, "mode", "realtime" if not current_force else "mouthpiece")
    if force_ask_agent is None:
        return {
            "ok": True,
            "status": "options_updated",
            "force_ask_agent": current_force,
            "mode": mode,
        }
    want = bool(force_ask_agent)
    if want != current_force:
        return {
            "ok": False,
            "status": "error",
            "error": "切换嘴替/Realtime 需挂断后重新开始通话",
            "force_ask_agent": current_force,
            "mode": mode,
            "needs_restart": True,
        }
    return {
        "ok": True,
        "status": "options_updated",
        "force_ask_agent": current_force,
        "mode": mode,
    }


async def _local_tool_handler(name: str, args: dict[str, Any]) -> str | None:
    """Handle realtime-only tools not in ToolRegistry. Return None to fall through.

    ask_agent is non-blocking: push to InputHub and return immediately. The active
    RealtimeSessionBridge bus subscription speaks each to_user_* as it arrives —
    waiting for to_user_final here used to freeze duplex for 10–180s.
    """
    n = (name or "").lower().replace(".", "__")
    if n in ("ask_agent", "ask_main_agent", "delegate_to_agent"):
        question = str(args.get("question") or args.get("query") or args.get("prompt") or "").strip()
        await ask_main_agent(question, wait_reply=False)
        # Signal execute path to skip model follow-up; bus TTS/speech will cover it.
        return "[VOICE_NO_REPLY] Delegated to main agent; spoken replies follow on the bus."
    return None


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


async def handle_mouthpiece_utterance(
    pcm16_b64: str,
    *,
    sample_rate: int = 24000,
) -> dict[str, Any]:
    """Process one whole utterance in mouthpiece (force) mode."""
    if _bridge is None:
        return {"ok": False, "status": "error", "error": "no active voice session"}
    if not getattr(_bridge, "force_ask_agent", False):
        return {
            "ok": False,
            "status": "error",
            "error": "mouthpiece utterance only valid in force/mouthpiece mode",
        }
    handler = getattr(_bridge, "handle_utterance", None)
    if handler is None:
        return {"ok": False, "status": "error", "error": "session does not support utterances"}
    await handler(pcm16_b64, sample_rate=sample_rate)
    return {"ok": True, "status": "accepted"}
