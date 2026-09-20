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


# ---------------------------------------------------------------------------
# Group-chat ASR must resolve to the BUILT-IN local service, never a cloud card
# ---------------------------------------------------------------------------

_CLOUD_ASR_CARD = {
    "base_url": "https://api.stepfun.com/step_plan/v1",
    "model_name": "stepaudio-2.5-asr",
    "group_asr": True,
}
_SENSEVOICE_CARD = {
    "builtin_service": "sensevoice",
    "asr_protocol": "openai_transcriptions",
    "model_name": "sensevoice-small",
}
_WHISPER_CARD = {
    "builtin_service": "whisper",
    "asr_protocol": "openai_transcriptions",
    "model_name": "base",
}


def _write_cards(cards_dir, cards: dict) -> str:
    """Materialise {name: fields} as model-card JSON files; return the dir path."""
    import json

    for name, fields in cards.items():
        (cards_dir / f"{name}.json").write_text(json.dumps({"name": name, **fields}), encoding="utf-8")
    return str(cards_dir)


def _resolve_with(cards_dir: str, *, enabled=lambda _svc: True):
    """Run resolve_group_asr_card() against a single-card-dir workspace."""
    from opensquad.audio import resolve_group_asr_card

    with (
        patch("opensquad.audio.syscfg.workspace_model_cards_dir", return_value=cards_dir),
        patch("opensquad.audio.syscfg.resource_search_dirs", return_value=[cards_dir]),
        patch("opensquad.audio.syscfg.is_service_enabled", side_effect=enabled),
    ):
        return resolve_group_asr_card()


def test_group_asr_prefers_builtin_over_flagged_cloud_card(tmp_path):
    """Regression: a cloud card flagged ``group_asr`` must not capture group voice input.

    ``stepaudio-2.5-asr`` was flagged group_asr=true and pointed at StepFun, whose
    API answers ``404 model_invalid`` — surfacing to every group member as
    "Group transcribe failed: 502 ... ASR HTTP 404".  The built-in local ASR must win.
    """
    cards = tmp_path / "model_cards"
    cards.mkdir()
    dirpath = _write_cards(
        cards,
        {
            "builtin-sensevoice-asr": _SENSEVOICE_CARD,
            "stepaudio-2.5-asr": _CLOUD_ASR_CARD,
        },
    )

    card = _resolve_with(dirpath)

    assert card is not None
    assert card["_card"] == "builtin-sensevoice-asr"
    assert card.get("builtin_service") == "sensevoice"
    assert asr_protocol_of(card) == "openai_transcriptions"


def test_group_asr_prefers_builtin_even_when_cloud_card_sorts_first(tmp_path):
    """Name order must not decide it — the built-in wins regardless of alphabetics."""
    cards = tmp_path / "model_cards"
    cards.mkdir()
    dirpath = _write_cards(
        cards,
        {
            "aaa-cloud-asr": _CLOUD_ASR_CARD,  # sorts before every builtin
            "builtin-whisper-asr": _WHISPER_CARD,
        },
    )

    card = _resolve_with(dirpath)

    assert card is not None
    assert card["_card"] == "builtin-whisper-asr"


def test_group_asr_prefers_enabled_builtin_service(tmp_path):
    """Between builtins, the service enabled in system_config wins over the fixed order."""
    cards = tmp_path / "model_cards"
    cards.mkdir()
    dirpath = _write_cards(
        cards,
        {"builtin-sensevoice-asr": _SENSEVOICE_CARD, "builtin-whisper-asr": _WHISPER_CARD},
    )

    card = _resolve_with(dirpath, enabled=lambda svc: svc == "whisper")

    assert card is not None
    assert card["_card"] == "builtin-whisper-asr"


def test_group_asr_defaults_to_sensevoice_when_both_enabled(tmp_path):
    cards = tmp_path / "model_cards"
    cards.mkdir()
    dirpath = _write_cards(
        cards,
        {"builtin-sensevoice-asr": _SENSEVOICE_CARD, "builtin-whisper-asr": _WHISPER_CARD},
    )

    card = _resolve_with(dirpath)

    assert card is not None
    assert card["_card"] == "builtin-sensevoice-asr"


def test_group_asr_falls_back_to_flagged_card_without_builtin(tmp_path):
    """An install shipping no built-in ASR card still gets the legacy behaviour."""
    cards = tmp_path / "model_cards"
    cards.mkdir()
    dirpath = _write_cards(cards, {"stepaudio-2.5-asr": _CLOUD_ASR_CARD})

    card = _resolve_with(dirpath)

    assert card is not None
    assert card["_card"] == "stepaudio-2.5-asr"


def test_group_asr_returns_none_when_unavailable(tmp_path):
    """Nothing built in and nothing flagged ⇒ caller tells the user to enable the service."""
    cards = tmp_path / "model_cards"
    cards.mkdir()
    dirpath = _write_cards(cards, {"gpt-5": {"is_image": False}})

    assert _resolve_with(dirpath) is None


def test_transcribe_file_disables_proxy_env_for_loopback(tmp_path):
    """Built-in ASR is a loopback service: an ambient HTTP_PROXY must not hijack it."""
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 100)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"text": "hi"}

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)

    async def _run(base_url):
        with patch("opensquad.audio.openai_asr.httpx.AsyncClient", return_value=mock_client) as mock_cls:
            await transcribe_file(api_key="sk-local", base_url=base_url, model="base", audio_path=str(audio))
            return mock_cls.call_args.kwargs["trust_env"]

    assert asyncio.run(_run("http://127.0.0.1:7101/v1")) is False
    # A cloud endpoint must keep the ambient proxy: some deployments require it.
    assert asyncio.run(_run("https://api.stepfun.com/step_plan/v1")) is True
