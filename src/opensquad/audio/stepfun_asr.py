"""StepFun ASR client — POST /v1/audio/asr/sse.

This is NOT the OpenAI Whisper API (``POST /v1/audio/transcriptions``).
StepFun uses a proprietary SSE JSON protocol with base64 audio payloads.
Keep this module named ``stepfun_asr`` until a separate OpenAI-compatible
transcriptions client exists.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import struct
from typing import Any

import httpx

from opensquad.audio import http_base_url

logger = logging.getLogger(__name__)


def _guess_format(path: str) -> dict[str, Any]:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in ("wav",):
        return {"type": "wav"}
    if ext in ("mp3",):
        return {"type": "mp3"}
    if ext in ("ogg", "opus"):
        return {"type": "ogg"}
    if ext in ("webm",):
        # browsers often record opus-in-webm; StepFun accepts ogg family best-effort
        return {"type": "ogg"}
    if ext in ("pcm", "raw"):
        return {"type": "pcm", "codec": "pcm_s16le", "rate": 16000, "bits": 16, "channel": 1}
    return {"type": "mp3"}


def pcm16le_to_wav_bytes(pcm: bytes, *, sample_rate: int = 24000, channels: int = 1) -> bytes:
    """Wrap raw PCM s16le mono/stereo into a WAV container."""
    bits = 16
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        data_size,
    )
    return header + pcm


async def _transcribe_b64(
    *,
    api_key: str,
    base_url: str,
    model: str,
    audio_b64: str,
    fmt: dict[str, Any],
    language: str = "zh",
    enable_itn: bool = True,
    timeout: float = 180.0,
    label: str = "audio",
) -> dict[str, Any]:
    payload = {
        "audio": {
            "data": audio_b64,
            "input": {
                "transcription": {
                    "model": model or "stepaudio-2.5-asr",
                    "language": language or "zh",
                    "enable_itn": bool(enable_itn),
                },
                "format": fmt,
            },
        }
    }
    url = f"{base_url.rstrip('/')}/audio/asr/sse"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    text_parts: list[str] = []
    final_text = ""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    return {
                        "success": False,
                        "error": f"ASR HTTP {resp.status_code}: {body[:500]!r}",
                    }
                event_name = "message"
                data_buf: list[str] = []
                async for line in resp.aiter_lines():
                    if line is None:
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip() or "message"
                        continue
                    if line.startswith("data:"):
                        data_buf.append(line[5:].lstrip())
                        continue
                    if line == "":
                        if not data_buf:
                            event_name = "message"
                            continue
                        data_str = "\n".join(data_buf)
                        data_buf = []
                        try:
                            obj = json.loads(data_str)
                        except json.JSONDecodeError:
                            event_name = "message"
                            continue
                        etype = obj.get("type") or event_name
                        if etype in ("transcript.text.delta", "transcript.delta"):
                            delta = obj.get("delta") or obj.get("text") or ""
                            if delta:
                                text_parts.append(delta)
                        elif etype in ("transcript.text.done", "transcript.done"):
                            final_text = obj.get("text") or "".join(text_parts)
                        elif isinstance(obj.get("text"), str) and obj.get("text"):
                            final_text = obj["text"]
                        event_name = "message"
                if data_buf:
                    try:
                        obj = json.loads("\n".join(data_buf))
                        final_text = obj.get("text") or final_text or "".join(text_parts)
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        logger.error("[stepfun_asr] request failed: %s", e)
        return {"success": False, "error": str(e)}

    text = (final_text or "".join(text_parts)).strip()
    if not text:
        logger.warning(
            "[stepfun_asr] empty transcript label=%s fmt=%s model=%s base=%s",
            label,
            fmt,
            model,
            base_url,
        )
        return {
            "success": False,
            "error": "ASR returned empty transcript (no speech detected or unsupported audio format)",
        }
    return {
        "success": True,
        "text": text,
        "language": language,
        "file": label,
        "mime": "audio/wav" if fmt.get("type") == "wav" else f"audio/{fmt.get('type') or 'bin'}",
    }


async def transcribe_file(
    *,
    api_key: str,
    base_url: str,
    model: str,
    audio_path: str,
    language: str = "zh",
    enable_itn: bool = True,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Transcribe a local audio file via StepFun ASR SSE API."""
    if not os.path.isfile(audio_path):
        return {"success": False, "error": f"File not found: {audio_path}"}

    work_path = audio_path
    cleanup: str | None = None
    ext = os.path.splitext(audio_path)[1].lower().lstrip(".")
    if ext in ("webm", "ogg", "opus"):
        converted = _ffmpeg_to_wav(audio_path)
        if converted:
            work_path = converted
            cleanup = converted

    try:
        with open(work_path, "rb") as f:
            raw = f.read()
    except Exception as e:
        if cleanup and os.path.isfile(cleanup):
            try:
                os.remove(cleanup)
            except OSError:
                pass
        return {"success": False, "error": f"Failed to read audio: {e}"}

    if len(raw) < 64:
        if cleanup and os.path.isfile(cleanup):
            try:
                os.remove(cleanup)
            except OSError:
                pass
        return {"success": False, "error": "ASR returned empty transcript (audio file too small)"}

    b64 = base64.b64encode(raw).decode("ascii")
    try:
        return await _transcribe_b64(
            api_key=api_key,
            base_url=base_url,
            model=model,
            audio_b64=b64,
            fmt=_guess_format(work_path),
            language=language,
            enable_itn=enable_itn,
            timeout=timeout,
            label=os.path.basename(audio_path),
        )
    finally:
        if cleanup and os.path.isfile(cleanup):
            try:
                os.remove(cleanup)
            except OSError:
                pass


def _ffmpeg_to_wav(src: str) -> str | None:
    """Convert browser webm/ogg to 16k mono wav via ffmpeg when available."""
    import shutil
    import subprocess
    import tempfile

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    fd, dst = tempfile.mkstemp(suffix=".wav", prefix="asr_")
    os.close(fd)
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                src,
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "wav",
                dst,
            ],
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) < 64:
            try:
                os.remove(dst)
            except OSError:
                pass
            logger.warning(
                "[stepfun_asr] ffmpeg convert failed rc=%s err=%s",
                proc.returncode,
                (proc.stderr or b"")[:300],
            )
            return None
        return dst
    except Exception as e:
        try:
            os.remove(dst)
        except OSError:
            pass
        logger.warning("[stepfun_asr] ffmpeg convert error: %s", e)
        return None


async def transcribe_bytes(
    *,
    api_key: str,
    base_url: str,
    model: str,
    audio: bytes,
    fmt: dict[str, Any] | None = None,
    language: str = "zh",
    enable_itn: bool = True,
    timeout: float = 180.0,
    label: str = "utterance.wav",
) -> dict[str, Any]:
    """Transcribe in-memory audio bytes (wav/pcm/mp3 payload as-is)."""
    if not audio:
        return {"success": False, "error": "empty audio"}
    b64 = base64.b64encode(audio).decode("ascii")
    return await _transcribe_b64(
        api_key=api_key,
        base_url=base_url,
        model=model,
        audio_b64=b64,
        fmt=fmt or {"type": "wav"},
        language=language,
        enable_itn=enable_itn,
        timeout=timeout,
        label=label,
    )


async def transcribe_pcm16le(
    *,
    api_key: str,
    base_url: str,
    model: str,
    pcm: bytes,
    sample_rate: int = 24000,
    language: str = "zh",
) -> dict[str, Any]:
    """Transcribe raw PCM16LE by wrapping as WAV."""
    if not pcm or len(pcm) < 320:
        return {"success": False, "error": "PCM too short"}
    wav = pcm16le_to_wav_bytes(pcm, sample_rate=int(sample_rate) or 24000)
    return await transcribe_bytes(
        api_key=api_key,
        base_url=base_url,
        model=model,
        audio=wav,
        fmt={"type": "wav"},
        language=language,
        label="utterance.wav",
    )


async def transcribe_with_card(
    card: dict[str, Any],
    audio_path: str,
    language: str = "zh",
) -> dict[str, Any]:
    return await transcribe_file(
        api_key=card.get("api_key") or "",
        base_url=http_base_url(card),
        model=card.get("model_name") or "stepaudio-2.5-asr",
        audio_path=audio_path,
        language=language,
    )


async def transcribe_pcm_with_card(
    card: dict[str, Any],
    pcm: bytes,
    *,
    sample_rate: int = 24000,
    language: str = "zh",
) -> dict[str, Any]:
    return await transcribe_pcm16le(
        api_key=card.get("api_key") or "",
        base_url=http_base_url(card),
        model=card.get("model_name") or "stepaudio-2.5-asr",
        pcm=pcm,
        sample_rate=sample_rate,
        language=language,
    )
