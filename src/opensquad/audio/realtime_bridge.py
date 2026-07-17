"""RealtimeSessionBridge — StepFun Realtime WS with OpenSquad tool integration."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

MAX_REALTIME_TOOLS = 32


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
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": fn.get("name", ""),
                        "description": fn.get("description") or fn.get("name") or "",
                        "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                    },
                }
            )
        elif t.get("name"):
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.get("name", ""),
                        "description": t.get("description") or t.get("name") or "",
                        "parameters": t.get("parameters") or {"type": "object", "properties": {}},
                    },
                }
            )
    return out


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
        tool_filter: str = "all",
    ):
        from opensquad.audio import ws_realtime_url

        self.card = card
        self.tool_registry = tool_registry
        self.instructions = instructions or "You are a helpful voice assistant."
        self.voice = voice or card.get("audio_output_voice") or "linjiajiejie"
        self.emit = emit or self._noop_emit
        self.tool_filter = tool_filter
        self.ws_url = ws_realtime_url(card)
        self.api_key = card.get("api_key") or ""
        self._ws = None
        self._recv_task: asyncio.Task | None = None
        self._closed = False

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
        try:
            self._ws = await websockets.connect(self.ws_url, additional_headers=headers, max_size=8 * 1024 * 1024)
        except TypeError:
            self._ws = await websockets.connect(self.ws_url, extra_headers=headers, max_size=8 * 1024 * 1024)

        tools: list[dict] = []
        try:
            if self.tool_registry and hasattr(self.tool_registry, "generate_openai_tools"):
                tools = openai_tools_to_stepfun(self.tool_registry.generate_openai_tools(self.tool_filter))
        except Exception as e:
            logger.warning("[RealtimeBridge] Failed to build tools: %s", e)

        session: dict[str, Any] = {
            "modalities": ["text", "audio"],
            "instructions": self.instructions,
            "voice": self.voice,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "turn_detection": {"type": "server_vad", "prefix_padding_ms": 500},
        }
        if tools:
            session["tools"] = tools
            session["instructions"] += (
                f"\n\nYou have up to {len(tools)} OpenSquad tools available. "
                "Call them when the user needs filesystem, search, or other actions."
            )

        await self._send({"type": "session.update", "session": session})
        self._recv_task = asyncio.create_task(self._recv_loop(), name="realtime-recv")
        await self.emit("voice_realtime_status", {"status": "connected", "tools": len(tools)})
        logger.info("[RealtimeBridge] Connected with %d tools", len(tools))

    async def stop(self) -> None:
        self._closed = True
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
        await self._send({"type": "input_audio_buffer.append", "audio": pcm16_b64})

    async def commit_audio(self) -> None:
        if self._closed or not self._ws:
            return
        await self._send({"type": "input_audio_buffer.commit"})
        await self._send({"type": "response.create"})

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

    async def _handle_event(self, event: dict) -> None:
        etype = event.get("type") or ""
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
            await self.emit(
                "voice_transcript",
                {"role": "user", "text": event.get("transcript") or "", "final": True},
            )
            return
        if etype == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                await self._handle_function_call(item)
            return
        if etype == "response.function_call_arguments.done":
            call_id = event.get("call_id") or ""
            name = event.get("name") or ""
            arguments = event.get("arguments") or "{}"
            if call_id and name:
                await self._handle_function_call(
                    {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}
                )
            return
        if etype == "error":
            err = event.get("error") or event
            await self.emit("voice_realtime_status", {"status": "error", "error": err})
            return
        if etype in ("session.created", "session.updated"):
            await self.emit("voice_realtime_status", {"status": etype, "session": event.get("session")})

    async def _handle_function_call(self, item: dict) -> None:
        call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:8]}"
        name = item.get("name") or ""
        raw_args = item.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except json.JSONDecodeError:
            args = {}

        await self.emit("tool_call", {"id": call_id, "name": name, "args": json.dumps(args, ensure_ascii=False)})
        await self.emit("voice_realtime_status", {"status": "tool_running", "tool": name})

        result_text = ""
        try:
            if not self.tool_registry:
                result_text = "Error: tool registry unavailable"
            else:
                result = await self.tool_registry.call(name, args if isinstance(args, dict) else {})
                if isinstance(result, dict) and result.get("__output_media__"):
                    await self.emit("output_media", result["__output_media__"])
                result_text = (
                    json.dumps(result, ensure_ascii=False, default=str) if not isinstance(result, str) else result
                )
        except Exception as e:
            result_text = f"Error: {e}"
            logger.error("[RealtimeBridge] tool %s failed: %s", name, e)

        preview = result_text[:2000]
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
        await self._send({"type": "response.create"})
        await self.emit("voice_realtime_status", {"status": "connected"})
