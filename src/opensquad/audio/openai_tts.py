"""OpenAI-compatible TTS client — POST /v1/audio/speech.

Works with any provider that implements the OpenAI Audio Speech API
(model / voice / input / response_format). Optional ``instruction`` is
passed through when the provider supports it.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import httpx

from opensquad.audio import http_base_url
from opensquad.system_config import syscfg

logger = logging.getLogger(__name__)

# Reuse TCP/TLS across many short TTS chunk requests (auto-speech pipeline).
_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=120.0)
    return _http


async def synthesize_speech(
    *,
    api_key: str,
    base_url: str,
    model: str,
    text: str,
    voice: str = "alloy",
    instruction: str = "",
    response_format: str = "mp3",
    timeout: float = 120.0,
    output_dir: str | None = None,
) -> dict[str, Any]:
    """Synthesize speech and save to uploads. Returns {success, url, path, mime}."""
    prompt = (text or "").strip()
    if not prompt:
        return {"success": False, "error": "text is required"}
    if not (api_key or "").strip():
        return {"success": False, "error": "api_key is required"}
    if not (base_url or "").strip():
        return {"success": False, "error": "base_url is required"}
    if not (model or "").strip():
        return {"success": False, "error": "model is required"}

    out_dir = output_dir or syscfg.workspace_uploads_dir()
    os.makedirs(out_dir, exist_ok=True)
    ext = "mp3" if response_format in ("mp3", "mpeg") else response_format or "mp3"
    fname = f"agent_tts_{uuid.uuid4().hex[:12]}.{ext}"
    fpath = os.path.join(out_dir, fname)

    url = f"{base_url.rstrip('/')}/audio/speech"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "model": model,
        "voice": voice or "alloy",
        "input": prompt,
        "response_format": ext,
    }
    if instruction:
        body["instruction"] = instruction

    try:
        resp = await _client().post(url, headers=headers, json=body, timeout=timeout)
        if resp.status_code >= 400:
            return {
                "success": False,
                "error": f"TTS HTTP {resp.status_code}: {resp.text[:500]}",
            }
        raw = resp.content
        if not raw:
            return {"success": False, "error": "TTS returned empty body"}
        with open(fpath, "wb") as f:
            f.write(raw)
    except Exception as e:
        logger.error("[openai_tts] request failed: %s", e)
        return {"success": False, "error": str(e)}

    mime = "audio/mpeg" if ext == "mp3" else f"audio/{ext}"
    return {
        "success": True,
        "url": f"/uploads/{fname}",
        "path": fpath,
        "mime": mime,
        "file": fname,
        "__output_media__": [{"type": "audio", "url": f"/uploads/{fname}", "mime": mime}],
    }


async def synthesize_with_card(
    card: dict[str, Any],
    text: str,
    voice: str = "",
    instruction: str = "",
    output_dir: str | None = None,
) -> dict[str, Any]:
    return await synthesize_speech(
        api_key=card.get("api_key") or "",
        base_url=http_base_url(card),
        model=card.get("model_name") or "",
        text=text,
        voice=voice or card.get("audio_output_voice") or "alloy",
        instruction=instruction,
        output_dir=output_dir,
    )
