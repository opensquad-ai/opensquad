"""Tests for websearch reranker sidecar startup decoupling."""

import os
import sys
import time
from unittest.mock import MagicMock

import pytest

_SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "plugins", "websearch", "service"))
_PLUGINS_DIR = os.path.abspath(os.path.join(_SERVICE_DIR, "..", ".."))
# Appended, not inserted: src/plugins holds packages that shadow installed ones
# (`plugins/telegram/` vs the PyPI `telegram`), so putting it first broke any
# later test that imports the real package.
for _d in (_SERVICE_DIR, _PLUGINS_DIR):
    if _d not in sys.path:
        sys.path.append(_d)

import reranker_sidecar


class _StubStore:
    """A store whose snapshot lookups are tests' to control."""

    active: str = ""

    @classmethod
    def active_snapshot_dir(cls) -> str:
        return cls.active

    @staticmethod
    def migrate_legacy_snapshot() -> str:
        return ""

    @staticmethod
    def is_complete() -> bool:
        return False

    @staticmethod
    def start_download(*_args, **_kwargs):
        return {}


@pytest.fixture(autouse=True)
def _stub_store(monkeypatch):
    """Never let a sidecar test reach the deployment's real 1.2GB weight dirs."""
    _StubStore.active = ""
    monkeypatch.setattr(reranker_sidecar, "_store_module", lambda: _StubStore)


def test_start_reranker_sidecar_returns_without_waiting_for_health(monkeypatch, tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model_dir / "model.safetensors").write_text("x", encoding="utf-8")

    proc = MagicMock()
    proc.pid = 12345
    proc.poll.return_value = None
    popen_calls = []

    monkeypatch.setenv("WEBSEARCH_RERANKER_ENABLED", "1")
    monkeypatch.setenv("WEBSEARCH_RERANKER_PORT", "18999")
    monkeypatch.setenv("WEBSEARCH_RERANKER_URL", "http://127.0.0.1:18999")
    monkeypatch.setattr(reranker_sidecar, "_model_path", lambda: str(model_dir))
    monkeypatch.setattr(reranker_sidecar, "_health_ok", lambda *args, **kwargs: False)
    monkeypatch.setattr(reranker_sidecar, "_port_open", lambda *args, **kwargs: False)
    monkeypatch.setattr(reranker_sidecar, "_reranker_deps_available", lambda: True)
    monkeypatch.setattr(reranker_sidecar, "_start_guardian", lambda: None)
    monkeypatch.setattr(reranker_sidecar.atexit, "register", lambda *args, **kwargs: None)

    def fake_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        return proc

    monkeypatch.setattr(reranker_sidecar.subprocess, "Popen", fake_popen)
    reranker_sidecar._reranker_proc = None

    started = time.perf_counter()
    reranker_sidecar.start_reranker_sidecar()
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0
    assert len(popen_calls) == 1
    assert reranker_sidecar._reranker_proc is proc


# ── the model path must resolve to wherever the weights actually are ──────────
# The store only downloads into the workspace, but the sidecar used to look at
# the install-dir path alone: a downloaded model was never found, the spawn
# printed "model missing … auto-downloading" and returned, and the reranker
# silently never started while the UI reported the model as ready.


@pytest.fixture
def no_install_dir_weights(monkeypatch, tmp_path):
    """Point the manual-deploy path at a directory that does not exist."""
    monkeypatch.setattr(reranker_sidecar, "_install_dir_snapshot", lambda: str(tmp_path / "install-dir" / "snap"))
    monkeypatch.delenv("WEBSEARCH_RERANKER_MODEL_PATH", raising=False)


def test_model_path_uses_the_install_dir_copy_when_it_is_there(monkeypatch, tmp_path):
    install = tmp_path / "install-dir" / "snap"
    install.mkdir(parents=True)
    workspace = tmp_path / "workspace" / "snap"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(reranker_sidecar, "_install_dir_snapshot", lambda: str(install))
    _StubStore.active = str(workspace)
    monkeypatch.delenv("WEBSEARCH_RERANKER_MODEL_PATH", raising=False)

    assert reranker_sidecar._model_path() == str(install)


def test_model_path_falls_back_to_the_store_snapshot(no_install_dir_weights, tmp_path):
    workspace = tmp_path / "workspace" / "snap"
    workspace.mkdir(parents=True)
    _StubStore.active = str(workspace)

    assert reranker_sidecar._model_path() == str(workspace)


def test_model_path_honours_the_explicit_override(monkeypatch, tmp_path):
    install = tmp_path / "install-dir" / "snap"
    install.mkdir(parents=True)
    monkeypatch.setattr(reranker_sidecar, "_install_dir_snapshot", lambda: str(install))
    _StubStore.active = str(install)
    monkeypatch.setenv("WEBSEARCH_RERANKER_MODEL_PATH", str(tmp_path / "explicit"))

    assert reranker_sidecar._model_path() == str(tmp_path / "explicit")


def test_model_path_falls_back_to_the_install_dir_when_nothing_exists(no_install_dir_weights, tmp_path):
    # Fresh machine, no weights anywhere: keep naming the manual-deploy path so
    # the "model missing … copy the weights" hint stays accurate.
    _StubStore.active = str(tmp_path / "nowhere")

    path = reranker_sidecar._model_path()

    assert os.path.join("install-dir", "snap") in path


def test_start_reranker_sidecar_asks_for_the_legacy_migration(monkeypatch, tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model_dir / "model.safetensors").write_text("x", encoding="utf-8")

    calls: list[str] = []
    monkeypatch.setattr(_StubStore, "migrate_legacy_snapshot", staticmethod(lambda: calls.append("called") or ""))

    proc = MagicMock()
    proc.pid = 1
    proc.poll.return_value = None
    monkeypatch.setenv("WEBSEARCH_RERANKER_ENABLED", "1")
    monkeypatch.setenv("WEBSEARCH_RERANKER_PORT", "18998")
    monkeypatch.setenv("WEBSEARCH_RERANKER_URL", "http://127.0.0.1:18998")
    monkeypatch.setattr(reranker_sidecar, "_model_path", lambda: str(model_dir))
    monkeypatch.setattr(reranker_sidecar, "_health_ok", lambda *a, **k: False)
    monkeypatch.setattr(reranker_sidecar, "_port_open", lambda *a, **k: False)
    monkeypatch.setattr(reranker_sidecar, "_reranker_deps_available", lambda: True)
    monkeypatch.setattr(reranker_sidecar, "_start_guardian", lambda: None)
    monkeypatch.setattr(reranker_sidecar.atexit, "register", lambda *a, **k: None)
    monkeypatch.setattr(reranker_sidecar.subprocess, "Popen", lambda *a, **k: proc)
    reranker_sidecar._reranker_proc = None

    reranker_sidecar.start_reranker_sidecar()

    assert calls == ["called"]
