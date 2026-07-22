"""Unit tests for OpenAI-compatible ASR and builtin Whisper card resolution."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from opensquad.audio import asr_protocol_of, resolve_asr_base_url
from opensquad.audio.openai_asr import transcribe_file


def test_asr_protocol_defaults_stepfun():
    assert asr_protocol_of(None) == "stepfun_sse"
    assert asr_protocol_of({"model_name": "stepaudio-2.5-asr"}) == "stepfun_sse"
    assert asr_protocol_of({"asr_protocol": "stepfun_sse"}) == "stepfun_sse"


def test_asr_protocol_openai_and_builtin_whisper():
    assert asr_protocol_of({"asr_protocol": "openai_transcriptions"}) == "openai_transcriptions"
    assert asr_protocol_of({"asr_protocol": "whisper"}) == "openai_transcriptions"
    assert asr_protocol_of({"builtin_service": "whisper"}) == "openai_transcriptions"
    assert (
        asr_protocol_of({"asr_protocol": "openai_transcriptions", "builtin_service": "whisper"})
        == "openai_transcriptions"
    )


def test_resolve_asr_base_url_builtin_whisper():
    with patch("opensquad.audio.syscfg.whisper_url", return_value="http://127.0.0.1:5001"):
        assert (
            resolve_asr_base_url(
                {
                    "builtin_service": "whisper",
                    "base_url": "http://ignored:9999/v1",
                }
            )
            == "http://127.0.0.1:5001/v1"
        )

    with patch("opensquad.audio.syscfg.whisper_url", return_value="http://127.0.0.1:5001/v1"):
        assert resolve_asr_base_url({"builtin_service": "whisper", "base_url": "x"}) == "http://127.0.0.1:5001/v1"

    assert resolve_asr_base_url({"base_url": "http://127.0.0.1:8080/v1/"}) == "http://127.0.0.1:8080/v1"


def test_resolve_asr_base_url_builtin_sensevoice():
    with patch("opensquad.audio.syscfg.sensevoice_url", return_value="http://127.0.0.1:7101"):
        assert (
            resolve_asr_base_url(
                {
                    "builtin_service": "sensevoice",
                    "base_url": "http://ignored:1/v1",
                }
            )
            == "http://127.0.0.1:7101/v1"
        )
    assert asr_protocol_of({"builtin_service": "sensevoice"}) == "openai_transcriptions"


def test_sensevoice_model_ready(tmp_path, monkeypatch):
    from plugins.sensevoice import model_store as ms

    monkeypatch.setattr(ms, "_workspace_root", lambda: str(tmp_path))
    model_path = tmp_path / "data" / "plugins" / "sensevoice" / "model"
    model_path.mkdir(parents=True)
    assert ms.model_ready(str(model_path)) is False
    for name in ms.REQUIRED_FILES:
        (model_path / name).write_bytes(b"x" * 10)
    assert ms.model_ready(str(model_path)) is True
    status = ms.get_status()
    assert status["ready"] is True
    assert status["model_dir"] == str(model_path)


def test_openai_asr_transcribe_file_success(tmp_path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 100)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"text": "你好世界", "language": "zh"}
    mock_resp.text = '{"text":"你好世界"}'

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=mock_resp)

    async def _run():
        with patch("opensquad.audio.openai_asr.httpx.AsyncClient", return_value=mock_client):
            return await transcribe_file(
                api_key="sk-local",
                base_url="http://127.0.0.1:5001/v1",
                model="base",
                audio_path=str(audio),
                language="zh",
            )

    result = asyncio.run(_run())
    assert result["success"] is True
    assert result["text"] == "你好世界"
    call_kwargs = mock_client.post.await_args
    assert call_kwargs.args[0] == "http://127.0.0.1:5001/v1/audio/transcriptions"
    assert "file" in call_kwargs.kwargs["files"]


def test_openai_asr_connect_error_hint(tmp_path):
    import httpx

    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 100)

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

    async def _run():
        with patch("opensquad.audio.openai_asr.httpx.AsyncClient", return_value=mock_client):
            return await transcribe_file(
                api_key="sk-local",
                base_url="http://127.0.0.1:5001/v1",
                model="base",
                audio_path=str(audio),
            )

    result = asyncio.run(_run())
    assert result["success"] is False
    assert "Whisper" in result["error"]


def test_transcribe_with_card_dispatches_openai(tmp_path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 100)
    card = {
        "asr_protocol": "openai_transcriptions",
        "api_key": "sk-local",
        "base_url": "http://127.0.0.1:8080/v1",
        "model_name": "whisper-1",
    }

    async def _run():
        with patch(
            "opensquad.audio.openai_asr.transcribe_with_card",
            new_callable=AsyncMock,
            return_value={"success": True, "text": "hi"},
        ) as mock_oai:
            from opensquad.audio.stepfun_asr import transcribe_with_card as dispatch

            result = await dispatch(card, str(audio), language="en")
            return result, mock_oai

    result, mock_oai = asyncio.run(_run())
    assert result["success"] is True
    assert result["text"] == "hi"
    mock_oai.assert_awaited_once()


def test_ensure_builtin_model_cards(tmp_path):
    from opensquad.workspace_utils import BUILTIN_MODEL_CARD_FILES, ensure_builtin_model_cards

    install = tmp_path / "install"
    ws = tmp_path / "ws"
    src = install / "model_cards"
    src.mkdir(parents=True)
    for name in BUILTIN_MODEL_CARD_FILES:
        (src / name).write_text('{"name": "builtin-whisper-asr"}', encoding="utf-8")

    copied = ensure_builtin_model_cards(workspace_path=str(ws), install_dir=str(install))
    assert copied == list(BUILTIN_MODEL_CARD_FILES)
    dst = ws / "model_cards" / "builtin-whisper-asr.json"
    assert dst.is_file()

    # Second call must not overwrite / re-copy
    dst.write_text('{"name": "user-edited"}', encoding="utf-8")
    copied2 = ensure_builtin_model_cards(workspace_path=str(ws), install_dir=str(install))
    assert copied2 == []
    assert '"user-edited"' in dst.read_text(encoding="utf-8")
