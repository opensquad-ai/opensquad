"""MouthpieceSession — force mode: ASR card → main Agent → TTS card (no Realtime WS)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
from collections import deque
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


def split_tts_chunks(text: str, *, max_chars: int = 80) -> list[str]:
    """Split speakable text into short chunks for lower TTS first-byte latency."""
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= max_chars:
        return [t]

    parts = re.split(r"(?<=[。！？!?；;\n])\s*", t)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        p = (part or "").strip()
        if not p:
            continue
        if not buf:
            buf = p
        elif len(buf) + len(p) + 1 <= max_chars:
            buf = f"{buf}{p}" if buf.endswith(("。", "！", "？", "!", "?", "；", ";", "\n")) else f"{buf}{p}"
        else:
            chunks.append(buf)
            buf = p
        # Hard-split oversized piece
        while len(buf) > max_chars:
            chunks.append(buf[:max_chars])
            buf = buf[max_chars:].lstrip()
    if buf:
        chunks.append(buf)
    return chunks or [t]


class MouthpieceSession:
    """Browser mic utterance → ASR → ask_main_agent → session TTS of every to_user.

    Design:
      - Mic utterances are always pushed to InputHub immediately (wait_reply=False).
      - A bus subscription speaks *every* to_user_final / to_user_reply / to_user_end_task
        during the call (fixes missing TTS after tool rounds).
      - TTS is sentence-chunked + queued for lower perceived latency.
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
        self._bus_sub_ids: list[str] = []
        self._tts_queue: asyncio.Queue[str] = asyncio.Queue()
        self._tts_worker: asyncio.Task | None = None
        self._recent_fps: deque[str] = deque(maxlen=24)
        self._audio_seq = 0

    @staticmethod
    async def _noop_emit(event_type: str, data: Any) -> None:
        return None

    async def start(self) -> None:
        self._closed = False
        self._audio_seq = 0
        self._recent_fps.clear()
        self._subscribe_agent_speech()
        self._tts_worker = asyncio.create_task(self._tts_worker_loop(), name="mouthpiece-tts")
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
            "[Mouthpiece] started ASR=%s TTS=%s voice=%s (session TTS on)",
            self.asr_card.get("_card") or self.asr_card.get("model_name"),
            self.tts_card.get("_card") or self.tts_card.get("model_name"),
            self.voice,
        )

    def _subscribe_agent_speech(self) -> None:
        from opensquad.events import bus

        for evt in ("to_user_final", "to_user_reply", "to_user_end_task"):
            sid = bus.subscribe(evt, self._on_agent_to_user)
            self._bus_sub_ids.append(sid)

    def _unsubscribe_agent_speech(self) -> None:
        from opensquad.events import bus

        for sid in self._bus_sub_ids:
            try:
                bus.unsubscribe_by_id(sid)
            except Exception:
                pass
        self._bus_sub_ids.clear()

    def _on_agent_to_user(self, data: Any) -> None:
        """Bus callback (sync): enqueue speakable agent text for TTS."""
        if self._closed:
            return
        from opensquad.audio.realtime_manager import (
            _unwrap_bus_payload,
            is_voice_no_reply,
            sanitize_for_tts,
        )

        raw = _unwrap_bus_payload(data)
        if not raw or is_voice_no_reply(raw):
            return
        spoken = sanitize_for_tts(raw) or raw.strip()
        if not spoken:
            return
        fp = hashlib.md5(spoken.encode("utf-8", errors="ignore"), usedforsecurity=False).hexdigest()[:16]
        if fp in self._recent_fps:
            return
        self._recent_fps.append(fp)
        try:
            self._tts_queue.put_nowait(spoken)
        except Exception:
            logger.warning("[Mouthpiece] TTS queue full/drop: %s", spoken[:80])

    async def _tts_worker_loop(self) -> None:
        from opensquad.audio.openai_tts import synthesize_with_card

        while not self._closed:
            try:
                spoken = await asyncio.wait_for(self._tts_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            if self._closed or not spoken:
                continue

            await self.emit(
                "voice_transcript",
                {"role": "assistant", "text": spoken, "final": True},
            )

            chunks = split_tts_chunks(spoken, max_chars=80)
            async with self._tts_lock:
                for chunk in chunks:
                    if self._closed:
                        break
                    await self.emit(
                        "voice_realtime_status",
                        {"status": "connected", "phase": "tts", "mode": "mouthpiece", "listening": True},
                    )
                    try:
                        tts = await synthesize_with_card(
                            self.tts_card,
                            text=chunk[:1000],
                            voice=self.voice,
                        )
                    except Exception as e:
                        logger.error("[Mouthpiece] TTS exception: %s", e)
                        continue
                    if not tts.get("success"):
                        logger.error("[Mouthpiece] TTS failed: %s", tts.get("error"))
                        continue
                    url = tts.get("url") or ""
                    if not url:
                        continue
                    mime = tts.get("mime") or "audio/mpeg"
                    self._audio_seq += 1
                    await self.emit(
                        "voice_audio_out",
                        {
                            "format": "mp3" if "mpeg" in mime or url.endswith(".mp3") else "audio",
                            "url": url,
                            "mime": mime,
                            "file": tts.get("file") or "",
                            "seq": self._audio_seq,
                            "queued": True,
                        },
                    )
                    logger.warning("[Mouthpiece] TTS chunk seq=%s url=%s", self._audio_seq, url)

            if not self._closed:
                await self.emit(
                    "voice_realtime_status",
                    {"status": "connected", "mode": "mouthpiece", "force_ask_agent": True, "listening": True},
                )

    async def stop(self) -> None:
        self._closed = True
        self._unsubscribe_agent_speech()
        if self._tts_worker and not self._tts_worker.done():
            self._tts_worker.cancel()
            try:
                await self._tts_worker
            except (asyncio.CancelledError, Exception):
                pass
        self._tts_worker = None
        for t in list(self._inflight):
            t.cancel()
        self._inflight.clear()
        # Drain queue
        while not self._tts_queue.empty():
            try:
                self._tts_queue.get_nowait()
            except Exception:
                break
        await self.emit("voice_realtime_status", {"status": "disconnected", "mode": "mouthpiece"})

    async def append_audio(self, pcm16_b64: str) -> None:
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
        from opensquad.audio.realtime_manager import ask_main_agent
        from opensquad.audio.stepfun_asr import transcribe_pcm_with_card

        try:
            pcm = base64.b64decode(pcm16_b64)
        except Exception as e:
            await self.emit(
                "voice_realtime_status",
                {
                    "status": "error",
                    "error": f"invalid PCM base64: {e}",
                    "mode": "mouthpiece",
                    "listening": True,
                },
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

        # Always push immediately; session bus subscription handles ALL TTS replies
        # (including post-tool turns). Waiting for the first to_user_final used to
        # drop later speech after tools finished.
        ask = self.ask_agent or ask_main_agent
        await self.emit(
            "voice_realtime_status",
            {
                "status": "tool_running",
                "phase": "ask_agent",
                "mode": "mouthpiece",
                "listening": True,
            },
        )
        self._busy = True
        try:
            try:
                await ask(text, wait_reply=False)
            except TypeError:
                await ask(text)
            except Exception as e:
                logger.error("[Mouthpiece] ask_agent push failed: %s", e)
                await self.emit(
                    "voice_realtime_status",
                    {"status": "error", "error": str(e), "mode": "mouthpiece", "listening": True},
                )
        finally:
            self._busy = False
            if not self._closed:
                await self.emit(
                    "voice_realtime_status",
                    {
                        "status": "connected",
                        "mode": "mouthpiece",
                        "force_ask_agent": True,
                        "listening": True,
                    },
                )
