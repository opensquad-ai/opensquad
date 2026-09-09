"""Gateway plugin uninstall must not surface launcher 404 for json-name mismatches."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

_BACKEND_ROOT = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
        "opensquad",
        "gateway",
        "backend",
    )
)
if _BACKEND_ROOT not in os.sys.path:
    os.sys.path.insert(0, _BACKEND_ROOT)

from app.ai_web.routes import _admin as admin  # noqa: E402


@pytest.mark.asyncio
async def test_uninstall_whisper_transcribe_survives_launcher_404(monkeypatch):
    paths: list[str] = []

    async def fake_delete(path: str, launcher_url: str | None = None):
        paths.append(path)
        name = path.rstrip("/").rsplit("/", 1)[-1]
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")

    monkeypatch.setattr(admin, "_proxy_delete", fake_delete)
    monkeypatch.setattr("opensquad.resource_uninstall.hide_plugin", lambda name: "whisper")
    monkeypatch.setattr("opensquad.resource_uninstall.is_protected_plugin", lambda name: False)
    monkeypatch.setattr(
        "opensquad.resource_uninstall.plugin_tombstone_ids",
        lambda name: ["whisper", "whisper_transcribe"],
    )

    result = await admin.admin_uninstall_plugin("whisper_transcribe", current_user=MagicMock())
    assert result["ok"] is True
    assert result["dir_name"] == "whisper"
    assert "/api/resources/plugins/whisper" in paths
    assert "/api/resources/plugins/whisper_transcribe" in paths
