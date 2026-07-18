"""MouthpieceSession — force mode: ASR card → main Agent → TTS card (no Realtime WS)."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class MouthpieceSession:
    """Browser mic utterance → ASR → ask_main_agent → TTS → voice_audio_out.

    Mic stays live during agent tool work: new utterances are ASR'd and pushed
    into InputHub immediately (as mid-work supplements). Only one waiter TTS
    pipeline runs at a time; barge-in speech uses wait_reply=False.
    """

    def __init__(
        self,
        *,
        asr_card: dict[str, Any],
        tts_card: dict[str, Any],
        voice: str = "",
        emit: Callable[[str, Any], Awaitable[None]] | None = None,
        ask_agent: Callable[..., Awaitable[str]] | None = None,
    ):
        self.asr_card = asr_card
        self.tts_card = tts_card
        self.voice = voice or tts_card.get("audio_output_voice") or "linjiajiejie"
        self.emit = emit or self._noop_emit
        self.ask_agent = ask_agent
        self.force_ask_agent = True
        self.mode = "mouthpiece"
        self._closed = False
        self._busy = False
        self._tts_lock = asyncio.Lock()
        self._inflight: set[asyncio.Task] = set()

    @staticmethod
    async def _noop_emit(event_type: str, data: Any) -> None:
        return None

    async def start(self) -> None:
        self._closed = False
        await self.emit(
            "voice_realtime_status",
            {
                "status": "connected",
                "mode": "mouthpiece",
                "force_ask_agent": True,
                "tools": 0,
                "listening": True,
            },
        )
        logger.warning(
            "[Mouthpiece] started ASR=%s TTS=%s voice=%s",
            self.asr_card.get("_card") or self.asr_card.get("model_name"),
            self.tts_card.get("_card") or self.tts_card.get("model_name"),
            self.voice,
        )

    async def stop(self) -> None:
        self._closed = True
        for t in list(self._inflight):
            t.cancel()
        self._inflight.clear()
        await self.emit("voice_realtime_status", {"status": "disconnected", "mode": "mouthpiece"})

    async def append_audio(self, pcm16_b64: str) -> None:
        # Mouthpiece ignores streaming uplink — frontend sends whole utterances.
        return None

    async def commit_audio(self) -> None:
        return None

    async def handle_utterance(
        self,
        pcm16_b64: str,
        *,
        sample_rate: int = 24000,
    ) -> None:
        if self._closed or not pcm16_b64:
            return
        # Do not block the mic path — process concurrently so speech during
        # agent tool calls is still recognized and pushed.
        task = asyncio.create_task(
            self._run_utterance_safe(pcm16_b64, sample_rate=sample_rate),
            name="mouthpiece-utterance",
        )
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    async def _run_utterance_safe(self, pcm16_b64: str, *, sample_rate: int) -> None:
        try:
            await self._run_utterance(pcm16_b64, sample_rate=sample_rate)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[Mouthpiece] utterance failed: %s", e)
            if not self._closed:
                await self.emit(
                    "voice_realtime_status",
                    {"status": "error", "error": str(e), "mode": "mouthpiece", "listening": True},
                )

    async def _run_utterance(self, pcm16_b64: str, *, sample_rate: int) -> None:
        from opensquad.audio.openai_tts import synthesize_with_card
        from opensquad.audio.realtime_manager import (
            _ask_lock,
            ask_main_agent,
            is_voice_no_reply,
            sanitize_for_tts,
        )
        from opensquad.audio.stepfun_asr import transcribe_pcm_with_card

        try:
            pcm = base64.b64decode(pcm16_b64)
        except Exception as e:
            await self.emit(
                "voice_realtime_status",
                {"status": "error", "error": f"invalid PCM base64: {e}", "mode": "mouthpiece", "listening": True},
            )
            return

        await self.emit(
            "voice_realtime_status",
            {"status": "connected", "phase": "asr", "mode": "mouthpiece", "listening": True},
        )

        asr = await transcribe_pcm_with_card(
            self.asr_card,
            pcm,
            sample_rate=int(sample_rate) or 24000,
            language="zh",
        )
        if self._closed:
            return
        if not asr.get("success"):
            err = asr.get("error") or "ASR failed"
            logger.error("[Mouthpiece] ASR failed: %s", err)
            await self.emit(
                "voice_realtime_status",
                {"status": "error", "error": str(err), "mode": "mouthpiece", "listening": True},
            )
            return

        text = (asr.get("text") or "").strip()
        if not text:
            return

        logger.warning("[Mouthpiece] ASR text: %s", text[:160])
        await self.emit(
            "voice_transcript",
            {"role": "user", "text": text, "final": True},
        )

        ask = self.ask_agent or ask_main_agent
        # If another utterance is already waiting for the agent reply, only push
        # this speech as a mid-work supplement (Runner merges it into the turn).
        agent_busy = _ask_lock.locked()
        await self.emit(
            "voice_realtime_status",
            {
                "status": "tool_running" if agent_busy else "connected",
                "phase": "ask_agent_supplement" if agent_busy else "ask_agent",
                "mode": "mouthpiece",
                "listening": True,
            },
        )

        if agent_busy:
            try:
                await ask(text, wait_reply=False)
            except TypeError:
                # Custom ask_agent without wait_reply — still call normally.
                await ask(text)
            except Exception as e:
                logger.error("[Mouthpiece] supplement push failed: %s", e)
            # Keep status listening; the active waiter will TTS the combined reply.
            if not self._closed:
                await self.emit(
                    "voice_realtime_status",
                    {"status": "tool_running", "phase": "agent_working", "mode": "mouthpiece", "listening": True},
                )
            return

        self._busy = True
        try:
            try:
                answer = await ask(text, wait_reply=True)
            except TypeError:
                answer = await ask(text)
            except Exception as e:
                answer = f"Error: ask_agent failed: {e}"
                logger.error("[Mouthpiece] ask_agent failed: %s", e)

            if self._closed:
                return

            preview = (answer or "").strip()
            if is_voice_no_reply(preview):
                logger.warning("[Mouthpiece] VOICE_NO_REPLY — stay silent")
                await self.emit(
                    "voice_realtime_status",
                    {"status": "connected", "phase": "no_reply", "mode": "mouthpiece", "listening": True},
                )
                return

            if not preview:
                preview = "（主 Agent 没有返回内容）"

            spoken = sanitize_for_tts(preview) or preview
            await self.emit(
                "voice_transcript",
                {"role": "assistant", "text": spoken, "final": True},
            )

            async with self._tts_lock:
                if self._closed:
                    return
                await self.emit(
                    "voice_realtime_status",
                    {"status": "connected", "phase": "tts", "mode": "mouthpiece", "listening": True},
                )
                tts = await synthesize_with_card(
                    self.tts_card,
                    text=spoken[:2000],
                    voice=self.voice,
                )
                if not tts.get("success"):
                    err = tts.get("error") or "TTS failed"
                    logger.error("[Mouthpiece] TTS failed: %s", err)
                    await self.emit(
                        "voice_realtime_status",
                        {"status": "error", "error": str(err), "mode": "mouthpiece", "listening": True},
                    )
                    return

                url = tts.get("url") or ""
                mime = tts.get("mime") or "audio/mpeg"
                await self.emit(
                    "voice_audio_out",
                    {
                        "format": "mp3" if "mpeg" in mime or url.endswith(".mp3") else "audio",
                        "url": url,
                        "mime": mime,
                        "file": tts.get("file") or "",
                    },
                )
                logger.warning("[Mouthpiece] TTS ready url=%s", url)
        finally:
            self._busy = False
            if not self._closed:
                await self.emit(
                    "voice_realtime_status",
                    {"status": "connected", "mode": "mouthpiece", "force_ask_agent": True, "listening": True},
                )
