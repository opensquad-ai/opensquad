"""RealtimeSessionBridge — StepFun Realtime WS with OpenSquad tool integration."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

MAX_REALTIME_TOOLS = 8
# StepFun examples use tiny outputs ("北京：晴，25°C"). Large dumps stall follow-up speech.
MAX_TOOL_OUTPUT_CHARS = 1200

# Custom function allowlist (OpenSquad registry names). Prefer StepFun built-in web_search
# for live web / weather — custom websearch follow-up was hanging after function_call_output.
_REALTIME_TOOL_ALLOW = {
    "system__get_time",
    "get_time",
}
_REALTIME_TOOL_BLOCK_SUBSTR = ("__wait", "__shell", "__run_command", "__python_exec")

# Map short StepFun tool names → registry names
_TOOL_NAME_TO_REGISTRY = {
    "get_time": "system__get_time",
}


def _tool_schema_name(t: dict) -> str:
    fn = t.get("function") if isinstance(t, dict) else None
    if isinstance(fn, dict):
        return str(fn.get("name") or "").strip()
    return str((t or {}).get("name") or "").strip()


def _realtime_tool_allowed(name: str) -> bool:
    n = (name or "").lower().replace(".", "__")
    if not n:
        return False
    if any(b in n for b in _REALTIME_TOOL_BLOCK_SUBSTR):
        return False
    return n in _REALTIME_TOOL_ALLOW or n.endswith("__get_time")


def openai_tools_to_stepfun(openai_tools: list[dict], limit: int = MAX_REALTIME_TOOLS) -> list[dict]:
    """Convert OpenAI ChatCompletions tool schemas to StepFun Realtime session.tools."""
    out: list[dict] = []
    for t in openai_tools or []:
        if len(out) >= limit:
            break
        if not isinstance(t, dict):
            continue
        if t.get("type") == "function" and isinstance(t.get("function"), dict):
            fn = t["function"]
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            # Prefer short names — StepFun examples use get_weather, not ns__fn.
            short = name.split("__")[-1] if "__" in name else name
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": short,
                        "description": fn.get("description") or short,
                        "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                    },
                }
            )
            _TOOL_NAME_TO_REGISTRY[short] = name.replace(".", "__")
        elif t.get("name"):
            name = str(t.get("name") or "").strip()
            if not name:
                continue
            short = name.split("__")[-1] if "__" in name else name
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": short,
                        "description": t.get("description") or short,
                        "parameters": t.get("parameters") or {"type": "object", "properties": {}},
                    },
                }
            )
            _TOOL_NAME_TO_REGISTRY[short] = name.replace(".", "__")
    return out


def _compact_tool_output(name: str, result: Any) -> str:
    """Shrink tool results so StepFun can speak a follow-up (large JSON stalls the turn)."""
    if isinstance(result, str):
        text = result
    else:
        # Prefer search hit list → short bullet summaries
        data = result
        if isinstance(result, dict):
            if isinstance(result.get("data"), list):
                data = result["data"]
            elif isinstance(result.get("results"), list):
                data = result["results"]
        if isinstance(data, list):
            lines: list[str] = []
            for i, item in enumerate(data[:5]):
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                snip = str(item.get("summary") or item.get("snippet") or "").strip()
                if title or snip:
                    lines.append(f"{i + 1}. {title}: {snip}"[:240])
            if lines:
                text = "Search results:\n" + "\n".join(lines)
            else:
                text = json.dumps(result, ensure_ascii=False, default=str)
        else:
            text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        text = text[: MAX_TOOL_OUTPUT_CHARS - 20] + "\n…(truncated)"
    return text


class RealtimeSessionBridge:
    """Browser PCM16 <-> StepFun Realtime, with OpenSquad ToolRegistry execution."""

    def __init__(
        self,
        *,
        card: dict[str, Any],
        tool_registry: Any,
        instructions: str = "",
        voice: str = "",
        emit: Callable[[str, Any], Awaitable[None]] | None = None,
        tool_filter: str | list[str] = "all",
        local_tool_handler: Callable[[str, dict[str, Any]], Awaitable[str | None]] | None = None,
        force_ask_agent: bool = True,
    ):
        from opensquad.audio import ws_realtime_url

        self.card = card
        self.tool_registry = tool_registry
        self.instructions = instructions or "You are a helpful voice assistant."
        self.emit = emit or self._noop_emit
        self.tool_filter = tool_filter
        self.local_tool_handler = local_tool_handler
        self.force_ask_agent = bool(force_ask_agent)
        self.ws_url = ws_realtime_url(card)
        self.api_key = card.get("api_key") or ""
        # StepFun realtime rejects OpenAI demo voices (alloy/echo/…). Prefer
        # card/voice config, then a known-good StepFun voice.
        _openai_demo = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
        raw_voice = (voice or card.get("audio_output_voice") or "").strip()
        if not raw_voice or raw_voice.lower() in _openai_demo:
            raw_voice = "linjiajiejie"
        self.voice = raw_voice
        self._ws = None
        self._recv_task: asyncio.Task | None = None
        self._closed = False
        self._pending_calls: dict[str, dict[str, str]] = {}
        self._handled_call_ids: set[str] = set()
        self._response_in_flight = False
        self._tools_busy = False
        self._followup_retries = 0
        self._awaiting_followup = False
        self._followup_watchdog: asyncio.Task | None = None
        self._seen_event_types: set[str] = set()
        self._auto_ask_task: asyncio.Task | None = None
        self._last_user_transcript = ""
        self._auto_ask_done_for: str = ""
        self._suppress_followup = False
        self._pending_response = False
        self._response_watchdog: asyncio.Task | None = None
        self._bus_sub_ids: list[str] = []
        self._speak_lock = asyncio.Lock()
        self._recent_spoken: list[str] = []
        self.mode = "realtime"

    @staticmethod
    async def _noop_emit(event_type: str, data: Any) -> None:
        return None

    async def start(self) -> None:
        if not self.api_key or str(self.api_key).startswith("YOUR_"):
            raise RuntimeError("Realtime model card api_key is missing")
        try:
            import websockets
        except ImportError as e:
            raise RuntimeError("websockets package required for realtime voice") from e

        headers = {"Authorization": f"Bearer {self.api_key}"}
        open_timeout = 15
        try:
            self._ws = await websockets.connect(
                self.ws_url,
                additional_headers=headers,
                max_size=8 * 1024 * 1024,
                open_timeout=open_timeout,
            )
        except TypeError:
            self._ws = await websockets.connect(
                self.ws_url,
                extra_headers=headers,
                max_size=8 * 1024 * 1024,
                open_timeout=open_timeout,
            )
        except Exception as e:
            raise RuntimeError(f"Realtime WS connect failed ({self.ws_url}): {e}") from e

        # Scheme 1: only ask_agent → same Agent Web main Runner. Skip registry tools
        # so the realtime model cannot bypass via get_time/websearch shortcuts.
        ask_tool = {
            "type": "function",
            "function": {
                "name": "ask_agent",
                "description": (
                    "REQUIRED for almost all user requests. Delegates to the main "
                    "OpenSquad Agent Web agent (same process/chat). Use for weather, "
                    "news, search, facts, files, coding, and any real-world question. "
                    "Pass the user's full utterance as question."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The user's full request in their language",
                        }
                    },
                    "required": ["question"],
                },
            },
        }
        tools: list[dict] = [ask_tool]

        session: dict[str, Any] = {
            "modalities": ["text", "audio"],
            "instructions": self.instructions,
            "voice": self.voice,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            # create_response=false: we drive replies on speech_stopped (not after ASR).
            "turn_detection": {
                "type": "server_vad",
                "prefix_padding_ms": 300,
                "silence_duration_ms": 600,
                "create_response": False,
            },
            "tools": tools,
            "tool_choice": "auto",
        }
        session["instructions"] += (
            "\n\nYou are a pure voice mouthpiece for the main Agent Web agent when "
            "force-delegate is on: do not invent answers; after tool/ask results, "
            "speak them concisely. If result is [VOICE_NO_REPLY], stay silent. "
            "When not force-delegating, you may answer greetings/time yourself or call ask_agent. "
            "Call ask_agent for weather/news/search/facts — spoken answers arrive separately; "
            "if tool result is [VOICE_NO_REPLY], stay silent and do not narrate the delegation."
        )

        await self._send({"type": "session.update", "session": session})
        self._recv_task = asyncio.create_task(self._recv_loop(), name="realtime-recv")
        self._subscribe_agent_speech()
        await self.emit(
            "voice_realtime_status",
            {"status": "connected", "tools": len(tools), "force_ask_agent": self.force_ask_agent},
        )
        logger.warning(
            "[RealtimeBridge] Connected tools=%d (ask_agent only) force_ask=%s url=%s",
            len(tools),
            self.force_ask_agent,
            self.ws_url,
        )

    async def stop(self) -> None:
        self._closed = True
        self._unsubscribe_agent_speech()
        if self._auto_ask_task and not self._auto_ask_task.done():
            self._auto_ask_task.cancel()
        if self._followup_watchdog and not self._followup_watchdog.done():
            self._followup_watchdog.cancel()
        if self._response_watchdog and not self._response_watchdog.done():
            self._response_watchdog.cancel()
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._recv_task
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None
        await self.emit("voice_realtime_status", {"status": "disconnected"})

    async def append_audio(self, pcm16_b64: str) -> None:
        if self._closed or not self._ws or not pcm16_b64:
            return
        # Keep streaming mic audio even while tools/agent work — barge-in ASR
        # must continue. (Previously dropped frames when _tools_busy.)
        await self._send({"type": "input_audio_buffer.append", "audio": pcm16_b64})

    async def commit_audio(self) -> None:
        if self._closed or not self._ws:
            return
        await self._send({"type": "input_audio_buffer.commit"})

    def _subscribe_agent_speech(self) -> None:
        """Speak every main-agent to_user_* while duplex call is live (like mouthpiece)."""
        if self.force_ask_agent:
            return
        from opensquad.events import bus

        self._unsubscribe_agent_speech()

        def _on(data: Any) -> None:
            if self._closed:
                return
            from opensquad.audio.realtime_manager import _unwrap_bus_payload, is_voice_no_reply

            text = _unwrap_bus_payload(data)
            if not text or is_voice_no_reply(text):
                return
            asyncio.create_task(self._speak_agent_text(text), name="realtime-agent-speak")

        for evt in ("to_user_reply", "to_user_final", "to_user_end_task"):
            sid = bus.subscribe(evt, _on, owner="realtime_duplex_speech")
            self._bus_sub_ids.append(sid)

    def _unsubscribe_agent_speech(self) -> None:
        if not self._bus_sub_ids:
            return
        from opensquad.events import bus

        for sid in self._bus_sub_ids:
            with contextlib.suppress(Exception):
                bus.unsubscribe_by_id(sid)
        self._bus_sub_ids.clear()

    async def _speak_agent_text(self, text: str) -> None:
        """Inject agent reply into realtime conversation and request speech."""
        from opensquad.audio.realtime_manager import sanitize_for_tts

        spoken = (sanitize_for_tts(text) or text or "").strip()
        if not spoken or self._closed:
            return
        # Dedup near-identical bursts (reply + final with same body).
        key = spoken[:160]
        if key in self._recent_spoken:
            return
        self._recent_spoken.append(key)
        if len(self._recent_spoken) > 12:
            self._recent_spoken = self._recent_spoken[-12:]

        async with self._speak_lock:
            if self._closed:
                return
            # Wait briefly if a model response is finishing; then speak.
            for _ in range(40):
                if self._closed:
                    return
                if not self._response_in_flight:
                    break
                await asyncio.sleep(0.1)
            speak = (
                "Speak the following answer to the user in their language. "
                "Be concise. Do not call tools. Do not invent extra facts. "
                "Plain speech only:\n\n"
                f"{spoken[:2500]}"
            )
            with contextlib.suppress(Exception):
                await self._send(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": speak}],
                        },
                    }
                )
            self._followup_retries = 0
            await self._request_followup_response()
            await self.emit("voice_realtime_status", {"status": "connected", "phase": "agent_speech"})

    def _queue_or_create_response(self, *, reason: str = "") -> None:
        """Create a model response, or queue if tools/response already in flight."""
        if self._closed:
            return
        if self._response_in_flight or self._tools_busy:
            self._pending_response = True
            logger.warning(
                "[RealtimeBridge] queue response.create (busy in_flight=%s tools=%s) reason=%s",
                self._response_in_flight,
                self._tools_busy,
                reason,
            )
            return
        asyncio.create_task(self._create_response_now(reason=reason), name="realtime-response-create")

    async def _create_response_now(self, *, reason: str = "") -> None:
        if self._closed or self._response_in_flight or self._tools_busy:
            self._pending_response = True
            return
        with contextlib.suppress(Exception):
            await self._send({"type": "response.create"})
            self._response_in_flight = True
            logger.warning("[RealtimeBridge] response.create reason=%s", reason or "unspecified")
            self._arm_response_watchdog()

    def _arm_response_watchdog(self) -> None:
        if self._response_watchdog and not self._response_watchdog.done():
            self._response_watchdog.cancel()
        self._response_watchdog = asyncio.create_task(self._response_watchdog_loop())

    def _clear_response_watchdog(self) -> None:
        if self._response_watchdog and not self._response_watchdog.done():
            self._response_watchdog.cancel()
        self._response_watchdog = None

    async def _response_watchdog_loop(self) -> None:
        """If response.created/done never arrives, clear flags so the call is not stuck."""
        try:
            await asyncio.sleep(8.0)
            if self._closed or not self._response_in_flight:
                return
            logger.error("[RealtimeBridge] response watchdog — clearing stuck in_flight")
            with contextlib.suppress(Exception):
                await self._send({"type": "response.cancel"})
            self._response_in_flight = False
            self._awaiting_followup = False
            await self.emit("voice_realtime_status", {"status": "connected", "phase": "watchdog_reset"})
            await self._flush_pending_response()
        except asyncio.CancelledError:
            return

    async def _flush_pending_response(self) -> None:
        if not self._pending_response or self._closed:
            return
        if self._response_in_flight or self._tools_busy:
            return
        self._pending_response = False
        await self._create_response_now(reason="flush_pending")

    async def _send(self, obj: dict) -> None:
        if not self._ws:
            return
        await self._ws.send(json.dumps(obj))

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if self._closed:
                    break
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[RealtimeBridge] recv loop error: %s", e)
            await self.emit("voice_realtime_status", {"status": "error", "error": str(e)})
        finally:
            if not self._closed:
                await self.emit("voice_realtime_status", {"status": "disconnected"})

    def _remember_call(self, *, call_id: str, name: str, arguments: str) -> None:
        if not call_id or not name:
            return
        self._pending_calls[call_id] = {
            "name": name,
            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments or {}, ensure_ascii=False),
        }

    async def _request_followup_response(self) -> None:
        """After function_call_output / agent speech: clear buffer, response.create, watch for stall."""
        with contextlib.suppress(Exception):
            await self._send({"type": "input_audio_buffer.clear"})
        await self._send({"type": "response.create"})
        self._response_in_flight = True
        self._awaiting_followup = True
        logger.warning("[RealtimeBridge] follow-up response.create sent")
        await self.emit(
            "voice_realtime_status",
            {"status": "tool_running", "phase": "awaiting_followup"},
        )
        self._arm_response_watchdog()
        if self._followup_watchdog and not self._followup_watchdog.done():
            self._followup_watchdog.cancel()
        self._followup_watchdog = asyncio.create_task(self._followup_watchdog_loop())

    async def _followup_watchdog_loop(self) -> None:
        try:
            await asyncio.sleep(2.5)
            if self._closed or not self._awaiting_followup:
                return
            if self._followup_retries >= 2:
                logger.error("[RealtimeBridge] follow-up still missing after retries — reset to connected")
                self._tools_busy = False
                self._awaiting_followup = False
                self._response_in_flight = False
                self._clear_response_watchdog()
                await self.emit(
                    "voice_realtime_status",
                    {"status": "connected", "phase": "followup_timeout"},
                )
                await self._flush_pending_response()
                return
            self._followup_retries += 1
            logger.warning(
                "[RealtimeBridge] no response.created after tool output — retry #%d",
                self._followup_retries,
            )
            with contextlib.suppress(Exception):
                await self._send({"type": "input_audio_buffer.clear"})
            await self._send({"type": "response.create"})
            self._response_in_flight = True
            self._arm_response_watchdog()
            self._followup_watchdog = asyncio.create_task(self._followup_watchdog_loop())
        except asyncio.CancelledError:
            return

    async def _handle_event(self, event: dict) -> None:
        etype = event.get("type") or ""
        if etype and etype not in self._seen_event_types:
            self._seen_event_types.add(etype)
            logger.warning("[RealtimeBridge] event type seen: %s", etype)

        if etype == "response.created":
            self._response_in_flight = True
            if self._awaiting_followup:
                self._awaiting_followup = False
                if self._followup_watchdog and not self._followup_watchdog.done():
                    self._followup_watchdog.cancel()
                logger.warning("[RealtimeBridge] follow-up response.created OK")
            return
        if etype == "input_audio_buffer.speech_started":
            # Barge-in: cancel idle model speech so the next utterance is heard promptly.
            if self._response_in_flight and not self._tools_busy and not self._awaiting_followup:
                logger.warning("[RealtimeBridge] speech_started → cancel in-flight response")
                with contextlib.suppress(Exception):
                    await self._send({"type": "response.cancel"})
            return
        if etype == "input_audio_buffer.speech_stopped":
            # Drive reply on VAD end — do not wait for ASR (was adding 1–10s+).
            if not self.force_ask_agent:
                self._queue_or_create_response(reason="speech_stopped")
            return
        if etype in ("response.cancelled", "response.cancel"):
            was_tools = self._tools_busy
            self._response_in_flight = False
            self._clear_response_watchdog()
            if was_tools and not self._closed and self._followup_retries < 1:
                self._followup_retries += 1
                logger.warning("[RealtimeBridge] follow-up cancelled while tools_busy — retrying")
                await self._request_followup_response()
                return
            self._tools_busy = False
            self._awaiting_followup = False
            self._followup_retries = 0
            logger.warning("[RealtimeBridge] response cancelled: %s", event)
            await self._flush_pending_response()
            return
        if etype in ("response.audio.delta", "response.output_audio.delta"):
            delta = event.get("delta") or ""
            if delta:
                await self.emit("voice_audio_out", {"audio": delta, "format": "pcm16"})
            return
        if etype in ("response.audio_transcript.delta", "response.output_audio_transcript.delta"):
            await self.emit(
                "voice_transcript", {"role": "assistant", "delta": event.get("delta") or "", "final": False}
            )
            return
        if etype in ("response.audio_transcript.done", "response.output_audio_transcript.done"):
            await self.emit(
                "voice_transcript",
                {"role": "assistant", "text": event.get("transcript") or "", "final": True},
            )
            return
        if etype == "conversation.item.input_audio_transcription.completed":
            text = (event.get("transcript") or "").strip()
            await self.emit(
                "voice_transcript",
                {"role": "user", "text": text, "final": True},
            )
            if text:
                self._last_user_transcript = text
                logger.warning("[RealtimeBridge] user transcript: %s", text[:160])
                # Non-force already created on speech_stopped; only force path needs transcript gate.
                if self.force_ask_agent:
                    self._schedule_auto_ask(text)
            return
        if etype == "response.function_call_arguments.done":
            call_id = event.get("call_id") or ""
            name = event.get("name") or ""
            arguments = event.get("arguments") or "{}"
            self._remember_call(call_id=call_id, name=name, arguments=arguments)
            self._tools_busy = True
            logger.warning("[RealtimeBridge] queued function_call %s name=%s", call_id, name)
            await self.emit("voice_realtime_status", {"status": "tool_running", "tool": name, "call_id": call_id})
            return
        if etype == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                call_id = item.get("call_id") or item.get("id") or ""
                name = item.get("name") or ""
                arguments = item.get("arguments") or "{}"
                if call_id and name and arguments and arguments != "{}":
                    self._remember_call(call_id=call_id, name=name, arguments=arguments)
                    self._tools_busy = True
            return
        if etype == "response.done":
            self._response_in_flight = False
            self._clear_response_watchdog()
            resp = event.get("response") or {}
            status = resp.get("status")
            if status and status not in ("completed", "incomplete"):
                logger.warning("[RealtimeBridge] response.done status=%s keys=%s", status, list(resp.keys()))
            for item in resp.get("output") or []:
                if not isinstance(item, dict) or item.get("type") != "function_call":
                    continue
                call_id = item.get("call_id") or item.get("id") or ""
                name = item.get("name") or ""
                arguments = item.get("arguments") or "{}"
                self._remember_call(call_id=call_id, name=name, arguments=arguments)
            pending = list(self._pending_calls.items())
            self._pending_calls.clear()
            if pending:
                self._tools_busy = True
                self._followup_retries = 0
                self._suppress_followup = False
                logger.warning("[RealtimeBridge] response.done → executing %d tool call(s)", len(pending))
                for call_id, meta in pending:
                    await self._execute_and_submit_tool(
                        call_id=call_id,
                        name=meta.get("name") or "",
                        raw_args=meta.get("arguments") or "{}",
                    )
                if self._suppress_followup:
                    logger.warning("[RealtimeBridge] suppress follow-up speech (VOICE_NO_REPLY / delegated)")
                    self._suppress_followup = False
                    self._tools_busy = False
                    self._awaiting_followup = False
                    self._followup_retries = 0
                    await self.emit("voice_realtime_status", {"status": "connected", "phase": "delegated"})
                    await self._flush_pending_response()
                else:
                    await self._request_followup_response()
            else:
                self._tools_busy = False
                self._awaiting_followup = False
                self._followup_retries = 0
                await self.emit("voice_realtime_status", {"status": "connected"})
                await self._flush_pending_response()
            return
        if etype == "error":
            err = event.get("error") or event
            logger.error("[RealtimeBridge] upstream error: %s", err)
            self._tools_busy = False
            self._response_in_flight = False
            self._awaiting_followup = False
            self._clear_response_watchdog()
            # Stay usable — do not freeze the call on transient upstream errors.
            await self.emit("voice_realtime_status", {"status": "connected", "phase": "upstream_error", "error": err})
            await self._flush_pending_response()
            return
        if etype in ("session.created", "session.updated"):
            await self.emit("voice_realtime_status", {"status": etype, "session": event.get("session")})

    @staticmethod
    def _is_time_tool(name: str) -> bool:
        n = (name or "").lower().replace(".", "__")
        return n.endswith("__get_time") or n in ("get_time", "system__get_time") or "get_time" in n

    def _schedule_auto_ask(self, text: str) -> None:
        if self._closed:
            return

        def _local_reply() -> None:
            self._queue_or_create_response(reason="local_reply")

        # Non-force: realtime model decides whether to call ask_agent.
        if not self.force_ask_agent:
            logger.warning("[RealtimeBridge] force_ask off — model decides: %s", text[:80])
            _local_reply()
            return

        if not self.local_tool_handler:
            _local_reply()
            return

        # Force = pure mouthpiece: every non-empty utterance (incl. greetings/time)
        # goes to main Agent Web; realtime only speaks the returned text.
        t = (text or "").strip()
        if len(t) < 1:
            return
        key = t
        if key == self._auto_ask_done_for:
            return

        # Mid-work barge-in: push as supplement without cancelling the in-flight ask.
        if self._tools_busy or (self._auto_ask_task and not self._auto_ask_task.done()):
            logger.warning("[RealtimeBridge] barge-in while busy → supplement push: %s", key[:80])
            self._auto_ask_done_for = key

            async def _supplement() -> None:
                try:
                    from opensquad.audio.realtime_manager import ask_main_agent

                    await ask_main_agent(key, wait_reply=False)
                except Exception as e:
                    logger.error("[RealtimeBridge] supplement push failed: %s", e)

            asyncio.create_task(_supplement(), name="realtime-ask-supplement")
            return

        logger.warning("[RealtimeBridge] force mouthpiece → Agent Web: %s", key[:80])
        self._auto_ask_task = asyncio.create_task(self._auto_ask_agent(key), name="realtime-auto-ask")

    async def _auto_ask_agent(self, text: str) -> None:
        """Force Scheme 1: voice transcript → main Agent Web Runner → speak result."""
        if self._closed or not self.local_tool_handler:
            return
        if text == self._auto_ask_done_for:
            return
        self._auto_ask_done_for = text
        self._tools_busy = True
        # Stop the realtime model from answering from its own knowledge.
        with contextlib.suppress(Exception):
            await self._send({"type": "response.cancel"})
        with contextlib.suppress(Exception):
            await self._send({"type": "input_audio_buffer.clear"})
        logger.warning("[RealtimeBridge] auto ask_agent → Agent Web: %s", text[:120])
        await self.emit(
            "voice_realtime_status",
            {"status": "tool_running", "tool": "ask_agent", "phase": "auto_delegate"},
        )
        try:
            # Force path needs the spoken answer synchronously (bus speech is for duplex only).
            from opensquad.audio.realtime_manager import ask_main_agent

            answer = await ask_main_agent(text, wait_reply=True)
        except Exception as e:
            answer = f"Error: ask_agent failed: {e}"
            logger.error("[RealtimeBridge] auto ask_agent failed: %s", e)
        if self._closed:
            return
        preview = (answer or "（主 Agent 没有返回内容）").strip()
        if len(preview) > 2500:
            preview = preview[:2480] + "\n…(truncated)"

        from opensquad.audio.realtime_manager import is_voice_no_reply, sanitize_for_tts

        await self.emit(
            "tool_result",
            {
                "id": f"auto-ask-{len(self._handled_call_ids)}",
                "name": "ask_agent",
                "args": json.dumps({"question": text}, ensure_ascii=False),
                "result": preview,
            },
        )
        if is_voice_no_reply(preview):
            logger.warning("[RealtimeBridge] main agent chose VOICE_NO_REPLY — stay silent")
            self._tools_busy = False
            self._awaiting_followup = False
            self._followup_retries = 0
            await self.emit("voice_realtime_status", {"status": "connected", "phase": "no_reply"})
            return
        spoken = sanitize_for_tts(preview) or preview
        speak = (
            "Speak the following answer to the user in their language. "
            "Be concise. Do not call tools. Do not invent extra facts. "
            "Do not say emoji names or the word 星号. Plain speech only:\n\n"
            f"{spoken}"
        )
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": speak}],
                },
            }
        )
        self._followup_retries = 0
        await self._request_followup_response()

    async def _execute_and_submit_tool(self, *, call_id: str, name: str, raw_args: str) -> None:
        if not call_id or call_id in self._handled_call_ids:
            return
        self._handled_call_ids.add(call_id)
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}

        await self.emit("tool_call", {"id": call_id, "name": name, "args": json.dumps(args, ensure_ascii=False)})
        await self.emit("voice_realtime_status", {"status": "tool_running", "tool": name, "call_id": call_id})

        result_obj: Any = None
        result_text = ""
        try:
            if self._is_time_tool(name):
                import time as _time
                from datetime import datetime

                now = datetime.now()
                result_obj = {
                    "status": "success",
                    "data": {
                        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "timestamp": int(_time.time()),
                        "timezone": _time.tzname[0] if _time.tzname else "",
                    },
                }
            elif self.local_tool_handler is not None:
                local = await self.local_tool_handler(name, args)
                if local is not None:
                    result_text = (
                        local if isinstance(local, str) else json.dumps(local, ensure_ascii=False, default=str)
                    )
                elif not self.tool_registry:
                    result_text = "Error: tool registry unavailable"
                else:
                    tool_name = _TOOL_NAME_TO_REGISTRY.get(name) or name
                    tool_name = (
                        tool_name.replace(".", "__") if "." in tool_name and "__" not in tool_name else tool_name
                    )
                    result_obj = await asyncio.wait_for(self.tool_registry.call(tool_name, args), timeout=45.0)
                    if isinstance(result_obj, dict) and result_obj.get("__output_media__"):
                        await self.emit("output_media", result_obj["__output_media__"])
            elif not self.tool_registry:
                result_text = "Error: tool registry unavailable"
            else:
                tool_name = _TOOL_NAME_TO_REGISTRY.get(name) or name
                tool_name = tool_name.replace(".", "__") if "." in tool_name and "__" not in tool_name else tool_name
                result_obj = await asyncio.wait_for(self.tool_registry.call(tool_name, args), timeout=45.0)
                if isinstance(result_obj, dict) and result_obj.get("__output_media__"):
                    await self.emit("output_media", result_obj["__output_media__"])
        except asyncio.TimeoutError:
            result_text = f"Error: tool {name} timed out"
            logger.error("[RealtimeBridge] tool %s timed out", name)
        except Exception as e:
            result_text = f"Error: {e}"
            logger.error("[RealtimeBridge] tool %s failed: %s", name, e)

        if result_obj is not None and not result_text:
            result_text = _compact_tool_output(name, result_obj)
        # ask_agent answers are meant to be spoken — allow a bit more text.
        max_chars = 2500 if "ask_agent" in (name or "").lower() else MAX_TOOL_OUTPUT_CHARS
        preview = (result_text or "Error: empty tool result")[:max_chars]

        from opensquad.audio.realtime_manager import is_voice_no_reply

        no_reply = "ask_agent" in (name or "").lower() and is_voice_no_reply(preview)
        if no_reply:
            self._suppress_followup = True
            # Tell the realtime model not to speak if a follow-up somehow still runs.
            preview = "[VOICE_NO_REPLY] Do not speak. End the turn silently."

        await self.emit(
            "tool_result",
            {"id": call_id, "name": name, "args": json.dumps(args, ensure_ascii=False), "result": preview},
        )
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {"type": "function_call_output", "call_id": call_id, "output": preview},
            }
        )
        logger.warning(
            "[RealtimeBridge] submitted function_call_output call_id=%s tool=%s out_len=%d no_reply=%s preview=%s",
            call_id,
            name,
            len(preview),
            no_reply,
            preview[:200],
        )
