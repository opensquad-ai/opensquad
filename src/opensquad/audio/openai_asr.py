"""OpenAI-compatible ASR client — POST /v1/audio/transcriptions.

Works with OpenAI Whisper API and any local server that implements the same
multipart endpoint (LocalAI, Faster-Whisper HTTP wrappers, OpenSquad Whisper
plugin ``/v1/audio/transcriptions``, etc.).
"""

from __future__ import annotations

import logging
import mimetypes
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_WHISPER_DOWN_HINT = (
    "Whisper ASR unavailable. Start the Whisper plugin service (or enable plugin auto_start) and retry."
)
_SENSEVOICE_DOWN_HINT = (
    "SenseVoice ASR unavailable. Open the SenseVoice plugin panel, "
    "download the model if needed, start the service, then retry."
)


def _connect_hint(base_url: str) -> str:
    u = (base_url or "").lower()
    if "7101" in u or "sensevoice" in u:
        return _SENSEVOICE_DOWN_HINT
    if "5001" in u or "whisper" in u:
        return _WHISPER_DOWN_HINT
    return f"ASR connect failed for {base_url}"


async def transcribe_file(
    *,
    api_key: str,
    base_url: str,
    model: str,
    audio_path: str,
    language: str = "zh",
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Transcribe a local audio file via OpenAI-compatible transcriptions API."""
    if not (base_url or "").strip():
        return {"success": False, "error": "base_url is required"}
    if not audio_path or not os.path.isfile(audio_path):
        return {"success": False, "error": f"audio file not found: {audio_path!r}"}

    url = f"{base_url.rstrip('/')}/audio/transcriptions"
    headers: dict[str, str] = {}
    key = (api_key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    filename = os.path.basename(audio_path) or "audio.wav"
    mime, _ = mimetypes.guess_type(filename)
    if not mime:
        mime = "application/octet-stream"

    data: dict[str, str] = {"model": (model or "whisper-1").strip() or "whisper-1"}
    lang = (language or "").strip()
    if lang:
        data["language"] = lang

    try:
        with open(audio_path, "rb") as f:
            file_bytes = f.read()
        if len(file_bytes) < 64:
            return {"success": False, "error": "audio file too small"}

        files = {"file": (filename, file_bytes, mime)}
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, data=data, files=files)
    except httpx.ConnectError as e:
        logger.warning("[openai_asr] connect failed: %s", e)
        return {"success": False, "error": _connect_hint(base_url)}
    except Exception as e:
        logger.error("[openai_asr] request failed: %s", e)
        return {"success": False, "error": str(e)}

    if resp.status_code >= 400:
        body = (resp.text or "")[:500]
        if resp.status_code in (502, 503, 504):
            return {"success": False, "error": f"{_connect_hint(base_url)} (HTTP {resp.status_code}: {body})"}
        return {"success": False, "error": f"ASR HTTP {resp.status_code}: {body}"}

    try:
        payload = resp.json()
    except Exception:
        text = (resp.text or "").strip()
        if text:
            return {"success": True, "text": text}
        return {"success": False, "error": "ASR returned non-JSON empty body"}

    if isinstance(payload, dict):
        if payload.get("success") is False:
            return {"success": False, "error": payload.get("error") or "ASR failed"}
        text = (payload.get("text") or "").strip()
        return {"success": True, "text": text, "language": payload.get("language")}

    return {"success": False, "error": f"unexpected ASR response type: {type(payload).__name__}"}


async def transcribe_bytes(
    *,
    api_key: str,
    base_url: str,
    model: str,
    audio: bytes,
    filename: str = "utterance.wav",
    language: str = "zh",
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Transcribe in-memory audio bytes via OpenAI-compatible transcriptions API."""
    if not audio:
        return {"success": False, "error": "empty audio"}
    if not (base_url or "").strip():
        return {"success": False, "error": "base_url is required"}

    import tempfile

    suffix = os.path.splitext(filename)[1] or ".wav"
    fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="oai_asr_")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(audio)
        return await transcribe_file(
            api_key=api_key,
            base_url=base_url,
            model=model,
            audio_path=tmp,
            language=language,
            timeout=timeout,
        )
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


async def transcribe_with_card(
    card: dict[str, Any],
    audio_path: str,
    language: str = "zh",
) -> dict[str, Any]:
    from opensquad.audio import resolve_asr_base_url

    return await transcribe_file(
        api_key=card.get("api_key") or "",
        base_url=resolve_asr_base_url(card),
        model=card.get("model_name") or "whisper-1",
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
    from opensquad.audio import resolve_asr_base_url
    from opensquad.audio.stepfun_asr import pcm16le_to_wav_bytes

    if not pcm or len(pcm) < 320:
        return {"success": False, "error": "PCM too short"}
    wav = pcm16le_to_wav_bytes(pcm, sample_rate=int(sample_rate) or 24000)
    return await transcribe_bytes(
        api_key=card.get("api_key") or "",
        base_url=resolve_asr_base_url(card),
        model=card.get("model_name") or "whisper-1",
        audio=wav,
        filename="utterance.wav",
        language=language,
    )
