"""Regression tests: ASR helpers must not block the event loop.

``stepfun_asr`` and ``openai_asr`` are awaited from FastAPI handlers that share a
single event loop with every WebSocket push.  A synchronous ``subprocess.run``
(ffmpeg, up to 60s) or ``open(...).read()`` inside those coroutines freezes the
whole backend for the duration of the call.

The contract these tests pin down:

* awaiting ``transcribe_file`` leaves the loop free to run other coroutines;
* the same work performed synchronously would not (the assertion is not vacuous).

A 50ms heartbeat coroutine runs alongside the call.  A blocked loop yields ~0
ticks, a free one yields ~10 per 0.5s of simulated work.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from opensquad.audio import openai_asr, stepfun_asr

_BLOCK_SECONDS = 0.6
_HEARTBEAT_SECONDS = 0.05
_MIN_TICKS = 4  # generous margin: a free loop ticks ~12, a blocked one 0-1


async def _run_with_heartbeat(coro_factory):
    """Await coro_factory() while counting heartbeat ticks."""
    ticks = 0
    stopped = False

    async def heartbeat():
        nonlocal ticks
        while not stopped:
            await asyncio.sleep(_HEARTBEAT_SECONDS)
            ticks += 1

    hb = asyncio.create_task(heartbeat())
    try:
        result = await coro_factory()
    finally:
        stopped = True
        await hb
    return result, ticks


def test_ffmpeg_conversion_does_not_block_event_loop(tmp_path, monkeypatch):
    """The ffmpeg subprocess must run off the event loop."""
    audio = tmp_path / "clip.webm"
    audio.write_bytes(b"x" * 4096)

    def slow_ffmpeg(src: str):
        time.sleep(_BLOCK_SECONDS)  # stands in for subprocess.run(ffmpeg, timeout=60)
        return None

    async def fake_transcribe_b64(**kwargs):
        return {"success": True, "text": "ok"}

    monkeypatch.setattr(stepfun_asr, "_ffmpeg_to_wav", slow_ffmpeg)
    monkeypatch.setattr(stepfun_asr, "_transcribe_b64", fake_transcribe_b64)

    async def call():
        return await stepfun_asr.transcribe_file(
            api_key="k",
            base_url="http://127.0.0.1:1",
            model="m",
            audio_path=str(audio),
        )

    result, ticks = asyncio.run(_run_with_heartbeat(call))

    assert result["success"] is True
    assert ticks >= _MIN_TICKS, f"event loop was blocked during ffmpeg conversion (ticks={ticks})"


def test_file_read_does_not_block_event_loop(tmp_path, monkeypatch):
    """Reading the recording must run off the event loop."""
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x" * 4096)

    real_read_bytes = Path.read_bytes

    def slow_read_bytes(self):
        time.sleep(_BLOCK_SECONDS)  # stands in for reading a multi-MB recording
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", slow_read_bytes)

    async def call():
        return await openai_asr.transcribe_file(
            api_key="k",
            base_url="http://127.0.0.1:1",
            model="whisper-1",
            audio_path=str(audio),
        )

    # The upload itself fails (no server listening) - only the loop freedom matters.
    _result, ticks = asyncio.run(_run_with_heartbeat(call))

    assert ticks >= _MIN_TICKS, f"event loop was blocked while reading audio (ticks={ticks})"
