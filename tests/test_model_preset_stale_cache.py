"""A stale preset cache must not outlive the build that ships the catalog.

Reported 2026-09-29: a deployment showed roughly a dozen providers (while the
catalog holds hundreds). The disk cache had been written by an older version and
every refresh since failed — models.dev unreachable from that network — so the
short list was served forever and the failure was silent: no visible refresh
control, no error, nothing in the UI saying the catalog was old.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

_BACKEND_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "src", "opensquad", "gateway", "backend")
)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.ai_web import model_preset_service as mps  # noqa: E402

DAY = 24 * 3600
BUNDLED_PROVIDERS = 40
OLD_CACHE_PROVIDERS = 12


def _provider(pid: str) -> dict:
    return {
        "id": pid,
        "label": pid,
        "provider": pid,
        "base_url": "https://example.invalid/v1",
        "api_protocol": "openai",
        "models": [],
    }


def _write_cache(path: Path, n_providers: int, *, age_days: float) -> None:
    path.write_text(json.dumps({"providers": [_provider(f"v{i}") for i in range(n_providers)]}), encoding="utf-8")
    stamp = time.time() - age_days * DAY
    os.utime(path, (stamp, stamp))


@pytest.fixture
def cache(tmp_path, monkeypatch):
    path = tmp_path / "model_preset_cache.json"
    monkeypatch.setattr(mps, "_cache_file_path", lambda: str(path))
    monkeypatch.setattr(
        mps, "_bundled_presets", lambda: {"providers": [_provider(f"b{i}") for i in range(BUNDLED_PROVIDERS)]}
    )
    monkeypatch.setattr(mps, "_cached_presets", {"providers": []})
    monkeypatch.setattr(mps, "_cached_source", "")
    return path


def test_stale_cache_is_replaced_by_the_bundled_list(cache):
    _write_cache(cache, OLD_CACHE_PROVIDERS, age_days=30)

    asyncio.run(mps.initialize())

    presets = mps.get_presets()
    assert len(presets["providers"]) == BUNDLED_PROVIDERS
    assert presets["meta"]["source"] == "static_fallback"
    assert presets["meta"]["cache_stale"] is True
    assert presets["meta"]["cache_age_seconds"] > 29 * DAY


def test_fresh_cache_wins_over_the_bundled_list(cache):
    _write_cache(cache, OLD_CACHE_PROVIDERS, age_days=0.1)

    asyncio.run(mps.initialize())

    presets = mps.get_presets()
    assert len(presets["providers"]) == OLD_CACHE_PROVIDERS
    assert presets["meta"]["source"] == "disk_cache"
    assert presets["meta"]["cache_stale"] is False


def test_a_stale_but_richer_cache_is_kept(cache):
    """Only a degraded cache is replaced: more data beats a newer file."""
    _write_cache(cache, BUNDLED_PROVIDERS + 10, age_days=30)

    asyncio.run(mps.initialize())

    assert len(mps.get_presets()["providers"]) == BUNDLED_PROVIDERS + 10
    assert mps.get_presets()["meta"]["source"] == "disk_cache"


def test_failed_refresh_falls_back_to_the_bundled_list(cache, monkeypatch):
    """Pressing refresh while models.dev is unreachable must still unstick a user."""
    _write_cache(cache, OLD_CACHE_PROVIDERS, age_days=30)
    asyncio.run(mps.initialize())

    async def unreachable():
        raise RuntimeError("models.dev unreachable")

    monkeypatch.setattr(mps, "_fetch_models_dev", unreachable)
    monkeypatch.setattr(mps, "_fetch_openrouter", unreachable)

    result = asyncio.run(mps.manual_refresh())

    assert result["ok"] is False
    assert result["providers"] == BUNDLED_PROVIDERS
    assert result["source"] == "static_fallback"
    assert any("bundled" in err for err in result["errors"]), result["errors"]
    assert len(cache.read_text(encoding="utf-8")) > 0  # the old cache is left alone on disk
