"""Regression guards for the plugin model-status defects reported from a pip install.

Both were reported after a ``v0.8.49-alpha.3`` pip install on Windows:

1. ``os.replace`` onto a status file that another process holds open for
   reading dies with ``[WinError 5] 拒绝访问:
   '…\\download_status.json.tmp' -> '…\\download_status.json'`` — the SenseVoice
   download aborted at 0%, before the first byte of the model was written.
   ``os.replace`` is atomic but needs DELETE access to the target, which
   Windows denies while a reader has it open; ``open()`` shares read/write but
   not delete, so the write must retry and then fall back to rewriting the
   target in place.
2. "``state == downloading`` with no thread in *this* process" is not evidence
   of an interruption. The status file is shared across processes (the plugin
   service writes it; the launcher, gateway and Electron read it), so a reader
   used to flip a live download to ``error`` and *persist* that — the UI then
   showed "Download interrupted" while the download kept running.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from plugins import _model_downloader as md
from plugins.sensevoice import model_store as ms


@pytest.fixture
def fast_retry(monkeypatch):
    """Exercise the retry path without the wall-clock backoff."""
    monkeypatch.setattr(md, "_STATUS_REPLACE_BACKOFF_S", 0.001)
    monkeypatch.setattr(md, "_STATUS_REPLACE_BACKOFF_MAX_S", 0.002)


@pytest.fixture
def sensevoice_status(tmp_path, monkeypatch):
    """Point the SenseVoice store at a throwaway status file, no local thread."""
    path = tmp_path / "download_status.json"
    monkeypatch.setattr(ms, "status_path", lambda: str(path))
    monkeypatch.setattr(ms, "_download_alive", lambda: False)
    return path


# ── 1. status writes must survive a concurrent reader, and never raise ─────


def test_status_write_survives_a_reader_holding_the_file_open(tmp_path, fast_retry):
    path = tmp_path / "download_status.json"
    md.write_status_json(path, {"state": "downloading", "progress": 10.0})

    # The reported case: another process reads the status file while we write.
    with open(path, encoding="utf-8") as held:
        held.read(1)
        md.write_status_json(path, {"state": "ready", "progress": 100.0})

    assert json.loads(path.read_text(encoding="utf-8"))["state"] == "ready"


def test_concurrent_status_writers_never_lose_the_file(tmp_path, fast_retry):
    path = tmp_path / "download_status.json"
    md.write_status_json(path, {"state": "idle"})
    errors: list[BaseException] = []
    stop = threading.Event()

    def writer(worker: int) -> None:
        try:
            for i in range(25):
                md.write_status_json(path, {"state": "downloading", "progress": worker * 100 + i})
        except BaseException as e:  # pragma: no cover - the assertion below reports it
            errors.append(e)

    def reader() -> None:
        # Mirrors the UI poll: tolerate a truncated read, never crash on it.
        while not stop.is_set():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass

    readers = [threading.Thread(target=reader, daemon=True) for _ in range(2)]
    for t in readers:
        t.start()
    writers = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    for t in readers:
        t.join(timeout=5)

    assert errors == []
    assert json.loads(path.read_text(encoding="utf-8"))["state"] == "downloading"


def test_status_write_never_raises_when_the_directory_is_unusable(tmp_path):
    # A file where the directory should be: makedirs fails, and the write must
    # still be a no-op rather than an exception inside a download loop.
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    md.write_status_json(blocker / "nested" / "download_status.json", {"state": "downloading"})


def test_status_is_stale_window():
    now = time.time()
    assert md.status_is_stale(now) is False
    assert md.status_is_stale(now - md.DOWNLOAD_STALE_SECONDS + 5) is False
    assert md.status_is_stale(now - md.DOWNLOAD_STALE_SECONDS - 5) is True
    assert md.status_is_stale(0) is True


# ── replace_with_retry: the mechanism the fix rests on ────────────────────


def test_replace_with_retry_recovers_from_a_transient_winerror(tmp_path, fast_retry, monkeypatch):
    src = tmp_path / "a.tmp"
    dst = tmp_path / "a"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "拒绝访问")
        return real_replace(a, b)

    monkeypatch.setattr(os, "replace", flaky)
    md.replace_with_retry(src, dst)

    assert dst.read_text(encoding="utf-8") == "new"
    assert calls["n"] == 3
    assert not src.exists()


def test_replace_with_retry_falls_back_to_an_in_place_rewrite(tmp_path, fast_retry, monkeypatch):
    src = tmp_path / "a.tmp"
    dst = tmp_path / "a"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old-content-that-is-longer", encoding="utf-8")

    def always_denied(a, b):
        raise PermissionError(5, "拒绝访问")

    monkeypatch.setattr(os, "replace", always_denied)
    md.replace_with_retry(src, dst)

    assert dst.read_text(encoding="utf-8") == "new"
    assert not src.exists()


# ── 2. readers are side-effect free; staleness is a time question ─────────


def test_read_status_keeps_a_fresh_download_owned_by_another_process(sensevoice_status):
    md.write_status_json(
        sensevoice_status,
        {"state": "downloading", "progress": 42.0, "updated_at": time.time()},
    )

    st = ms.read_status()

    assert st["state"] == "downloading"
    # A reader must not persist anything — the download is still running.
    assert json.loads(sensevoice_status.read_text(encoding="utf-8"))["state"] == "downloading"


def test_read_status_reports_a_stale_download_as_interrupted(sensevoice_status):
    md.write_status_json(
        sensevoice_status,
        {"state": "downloading", "progress": 42.0, "updated_at": time.time() - 3600},
    )

    st = ms.read_status()

    assert st["state"] == "error"
    assert "interrupted" in st["message"]
    # Reported, not written: the owning process (if any) still owns the file.
    assert json.loads(sensevoice_status.read_text(encoding="utf-8"))["state"] == "downloading"


def test_read_status_keeps_a_local_live_download(sensevoice_status, monkeypatch):
    monkeypatch.setattr(ms, "_download_alive", lambda: True)
    md.write_status_json(sensevoice_status, {"state": "downloading", "updated_at": time.time() - 3600})

    assert ms.read_status()["state"] == "downloading"


def test_get_status_self_heals_a_complete_model(tmp_path, monkeypatch, sensevoice_status):
    monkeypatch.setattr(ms, "model_ready", lambda *a, **k: True)
    monkeypatch.setattr(ms, "model_dir", lambda: str(tmp_path))
    md.write_status_json(
        sensevoice_status,
        {"state": "error", "message": "Download failed: HTTP 502", "updated_at": time.time() - 3600},
    )

    st = ms.get_status()

    assert st["ready"] is True
    assert st["download"]["state"] == "ready"
    assert json.loads(sensevoice_status.read_text(encoding="utf-8"))["state"] == "ready"


def test_get_status_keeps_an_error_while_the_model_is_incomplete(tmp_path, monkeypatch, sensevoice_status):
    monkeypatch.setattr(ms, "model_ready", lambda *a, **k: False)
    monkeypatch.setattr(ms, "model_dir", lambda: str(tmp_path))
    md.write_status_json(
        sensevoice_status,
        {"state": "error", "message": "Download failed: HTTP 502", "updated_at": time.time()},
    )

    assert ms.get_status()["download"]["state"] == "error"


# ── the shared ModelStore (websearch reranker) carries the same guard ─────


def _store(tmp_path) -> md.ModelStore:
    return md.ModelStore(
        plugin_name="websearch",
        model_dir=tmp_path / "model",
        status_path=tmp_path / "model_status.json",
    )


def test_model_store_keeps_a_fresh_download_owned_by_another_process(tmp_path):
    store = _store(tmp_path)
    payload = {**md.DownloadStatus(state="downloading", progress=33.0).to_dict(), "updated_at": time.time()}
    md.write_status_json(store.status_path, payload)

    assert store.get_status()["state"] == "downloading"


def test_model_store_reports_a_stale_download_as_interrupted(tmp_path):
    store = _store(tmp_path)
    payload = {**md.DownloadStatus(state="downloading", progress=33.0).to_dict(), "updated_at": time.time() - 3600}
    md.write_status_json(store.status_path, payload)

    assert store.get_status()["state"] == "error"


# ── shape guards: the fix cannot silently revert ─────────────────────────


def test_sensevoice_module_has_no_bare_replace():
    """Every status/model write there must go through the retrying helper."""
    text = Path(ms.__file__).read_text(encoding="utf-8")
    assert "os.replace(" not in text, (
        "sensevoice/model_store.py must use replace_with_retry: a bare os.replace "
        "onto a target another process is reading fails with WinError 5"
    )


def test_model_downloader_has_a_single_replace_site():
    text = Path(md.__file__).read_text(encoding="utf-8")
    assert text.count("os.replace(") == 1, "os.replace must live only inside replace_with_retry"
