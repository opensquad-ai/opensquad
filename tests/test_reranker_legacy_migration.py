"""The reranker weights must survive an upgrade.

The store downloads into the *workspace* (``{workspace}/data/plugins/websearch/
reranker``) — user data — but the legacy manual-deploy path sits inside the
installed plugin tree (``plugins/websearch/service/reranker/models/...``), i.e.
the very tree that ``pip install -U`` or a desktop installer replaces. A
deployment whose only copy lived there would silently need a fresh 1.2GB fetch,
so the store carries the install-dir weights into the workspace once.

Same-family defect found while wiring that up: the sidecar resolved its model
path from the install dir *only*, so a model the store had downloaded (every UI
download and every auto-download) was never found — the spawn path printed
"model missing … auto-downloading" and returned, and the reranker silently never
started while the UI reported the model as ready.
"""

from __future__ import annotations

import time

import pytest

from plugins.websearch import reranker_model_store as store


class _IdleStore:
    """Stand-in for the module's ModelStore singleton (never touches the disk)."""

    def is_running(self) -> bool:
        return False


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """Wire the workspace snapshot and the install-dir snapshot to tmp dirs."""
    workspace_snap = tmp_path / "ws" / "snapshots" / store.SNAPSHOT_REV
    legacy_snap = tmp_path / "install" / "snapshots" / store.SNAPSHOT_REV
    monkeypatch.setattr(store, "_snapshot_dir", lambda: str(workspace_snap))
    monkeypatch.setattr(store, "_legacy_snapshot_dir", lambda: str(legacy_snap))
    monkeypatch.setattr(store, "_get_store", lambda: _IdleStore())
    monkeypatch.setattr(store, "_migration_done", False)
    return workspace_snap, legacy_snap


def _fill(snap) -> None:
    snap.mkdir(parents=True, exist_ok=True)
    for name in store.REQUIRED_FILES:
        (snap / name).write_text("{}", encoding="utf-8")
    (snap / "model.safetensors").write_bytes(b"weights")


def _wait_until_complete(snap, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if store._snapshot_flat_complete(str(snap)):
            return True
        time.sleep(0.05)
    return store._snapshot_flat_complete(str(snap))


# ── the migration itself ─────────────────────────────────────────────────


def test_install_dir_weights_are_moved_into_the_workspace(layout):
    workspace_snap, legacy_snap = layout
    _fill(legacy_snap)

    note = store.migrate_legacy_snapshot()

    assert "install-dir weights" in note
    assert store._snapshot_flat_complete(str(workspace_snap))
    assert store.is_complete()


def test_workspace_weights_are_left_alone(layout):
    workspace_snap, legacy_snap = layout
    _fill(workspace_snap)
    _fill(legacy_snap)

    assert store.migrate_legacy_snapshot() == ""

    assert store._snapshot_flat_complete(str(legacy_snap)), "nothing to move — the install-dir copy stays"


def test_nothing_to_migrate_without_install_dir_weights(layout):
    workspace_snap, _legacy_snap = layout

    assert store.migrate_legacy_snapshot() == ""

    assert not workspace_snap.exists()


def test_migration_runs_once_per_process(layout):
    workspace_snap, legacy_snap = layout
    _fill(legacy_snap)

    assert store.migrate_legacy_snapshot() != ""
    assert store.migrate_legacy_snapshot() == ""
    assert store._snapshot_flat_complete(str(workspace_snap))


def test_rename_failure_falls_back_to_a_background_copy(layout, monkeypatch):
    workspace_snap, legacy_snap = layout
    _fill(legacy_snap)

    def _denied(*_args, **_kwargs):
        raise PermissionError(5, "拒绝访问")

    monkeypatch.setattr(store.os, "replace", _denied)

    note = store.migrate_legacy_snapshot()

    assert "copying" in note
    assert _wait_until_complete(workspace_snap), "the background copy must land the weights"
    assert store._snapshot_flat_complete(str(legacy_snap)), "a copy must never delete the install-dir weights"


def test_migration_is_skipped_while_a_download_is_running(layout, monkeypatch):
    workspace_snap, legacy_snap = layout
    _fill(legacy_snap)

    class _Running:
        def is_running(self) -> bool:
            return True

    monkeypatch.setattr(store, "_get_store", lambda: _Running())

    assert store.migrate_legacy_snapshot() == ""

    assert not workspace_snap.exists(), "the running download owns the workspace target"


# ── which copy is considered active ──────────────────────────────────────


def test_active_snapshot_dir_prefers_the_workspace(layout):
    workspace_snap, legacy_snap = layout
    _fill(workspace_snap)
    _fill(legacy_snap)

    assert store.active_snapshot_dir() == str(workspace_snap)


def test_active_snapshot_dir_falls_back_to_the_install_dir(layout):
    _workspace_snap, legacy_snap = layout
    _fill(legacy_snap)

    assert store.active_snapshot_dir() == str(legacy_snap)
