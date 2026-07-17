"""StepFun ASR client — POST /v1/audio/asr/sse."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
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
        return {"type": "ogg"}  # many browsers record opus-in-webm; StepFun accepts ogg family best-effort
    if ext in ("pcm", "raw"):
        return {"type": "pcm", "codec": "pcm_s16le", "rate": 16000, "bits": 16, "channel": 1}
    return {"type": "mp3"}


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
    try:
        with open(audio_path, "rb") as f:
            raw = f.read()
    except Exception as e:
        return {"success": False, "error": f"Failed to read audio: {e}"}

    b64 = base64.b64encode(raw).decode("ascii")
    payload = {
        "audio": {
            "data": b64,
            "input": {
                "transcription": {
                    "model": model or "stepaudio-2.5-asr",
                    "language": language or "zh",
                    "enable_itn": bool(enable_itn),
                },
                "format": _guess_format(audio_path),
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
                # Flush trailing buffer
                if data_buf:
                    try:
                        obj = json.loads("\n".join(data_buf))
                        final_text = obj.get("text") or final_text or "".join(text_parts)
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        logger.error("[StepFunASR] request failed: %s", e)
        return {"success": False, "error": str(e)}

    text = (final_text or "".join(text_parts)).strip()
    if not text:
        return {"success": False, "error": "ASR returned empty transcript"}
    return {
        "success": True,
        "text": text,
        "language": language,
        "file": os.path.basename(audio_path),
        "mime": mimetypes.guess_type(audio_path)[0],
    }


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
