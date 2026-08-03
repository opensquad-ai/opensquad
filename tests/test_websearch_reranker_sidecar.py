"""Tests for websearch reranker sidecar startup decoupling."""

import os
import sys
import time
from unittest.mock import MagicMock

_SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "plugins", "websearch", "service"))
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)

import reranker_sidecar


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
