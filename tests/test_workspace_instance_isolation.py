"""Two installations on one machine must not share "the last workspace I used".

Reported from the field: a source checkout (`pip install -e .`) and the packaged desktop app both
ended up on the same workspace, fighting over ports 9555/9600/9720, chat.db and plugin data. The
mechanism was one machine-global pointer — ``~/.opensquad/last_workspace.json`` — which the desktop
app wrote from its own (different) workspace resolution, so each install re-pointed the other.

These tests pin the two halves of the fix:

* the pointer is per installation now (``instance_slug()``), with a one-time read of the legacy file
  so nobody's existing workspace is lost, and the legacy file is never written again;
* a workspace can only be owned by one installation at a time (``ensure_workspace_owner``): a sibling
  service of the same installation passes, another installation is refused with an actionable error,
  and a dead owner's lock is taken over rather than blocking forever.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import opensquad.workspace_utils as wu  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    """Point the global config dir at a temp dir and reset caches between tests."""
    monkeypatch.setattr(wu, "GLOBAL_CONFIG_DIR", tmp_path / "global")
    monkeypatch.setattr(wu, "LEGACY_LAST_WORKSPACE_FILE", tmp_path / "global" / "last_workspace.json")
    wu._RUN_LOCKS.clear()
    wu._last_workspace_cache = {}
    wu._last_workspace_cache_loaded = False
    yield
    wu._RUN_LOCKS.clear()
    wu._last_workspace_cache = {}
    wu._last_workspace_cache_loaded = False


def _ws(tmp_path: Path, name: str) -> Path:
    ws = tmp_path / name
    (ws / ".opensquad").mkdir(parents=True, exist_ok=True)
    return ws


def test_each_installation_has_its_own_pointer(tmp_path, monkeypatch):
    """The whole point: A's choice is invisible to B."""
    a = _ws(tmp_path, "ws-a")
    b = _ws(tmp_path, "ws-b")

    monkeypatch.setenv("OPENSQUAD_INSTANCE", "install-a")
    assert wu.last_workspace_path() != wu.instance_config_dir() / "nope"
    wu.save_last_workspace(str(a))

    monkeypatch.setenv("OPENSQUAD_INSTANCE", "install-b")
    assert wu.read_last_workspace_path() is None, "B saw A's workspace"
    # B saves its own choice; A's pointer must not move.
    wu.save_last_workspace(str(b))
    assert wu.read_last_workspace_path() == str(b)

    monkeypatch.setenv("OPENSQUAD_INSTANCE", "install-a")
    assert wu.read_last_workspace_path() == str(a)

    # And the slug is derived from the install root when no override is set.
    monkeypatch.delenv("OPENSQUAD_INSTANCE", raising=False)
    slug = wu.instance_slug()
    assert slug and "/" not in slug and "\\" not in slug
    assert wu.instance_slug() == slug, "the slug must be stable across calls"


def test_the_legacy_pointer_is_read_once_and_never_written(tmp_path, monkeypatch):
    """Upgrade path: keep the workspace the user was on, then stop touching the shared file."""
    monkeypatch.setenv("OPENSQUAD_INSTANCE", "install-a")
    legacy = wu.LEGACY_LAST_WORKSPACE_FILE
    legacy.parent.mkdir(parents=True, exist_ok=True)
    old_ws = _ws(tmp_path, "old-ws")
    legacy.write_text(json.dumps({"last_workspace": str(old_ws)}), encoding="utf-8")

    # No instance file yet → the legacy value is read (the upgrade path).
    assert wu.read_last_workspace_path() == str(old_ws)

    before = legacy.read_text(encoding="utf-8")
    new_ws = _ws(tmp_path, "new-ws")
    wu.save_last_workspace(str(new_ws))

    assert legacy.read_text(encoding="utf-8") == before, "the shared pointer was written again"
    assert wu.last_workspace_path().exists()
    assert wu.read_last_workspace_path() == str(new_ws)


def test_a_sibling_service_of_the_same_installation_may_share(tmp_path, monkeypatch):
    """gateway + launcher + registry are one installation and must all start."""
    monkeypatch.setenv("OPENSQUAD_INSTANCE", "install-a")
    ws = _ws(tmp_path, "ws")

    wu.ensure_workspace_owner(str(ws))
    wu._RUN_LOCKS.clear()  # a second process of the same install
    wu.ensure_workspace_owner(str(ws))  # must not raise

    owner = json.loads((ws / ".opensquad" / "instance.json").read_text(encoding="utf-8"))
    assert owner["slug"] == "install-a"


def test_another_installation_is_refused_with_an_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSQUAD_INSTANCE", "install-a")
    ws = _ws(tmp_path, "ws")
    # Hold A's lock with a live reference: the OS lock lives as long as its handle does,
    # which is exactly the liveness signal another installation is refused on.
    lock_a = wu.WorkspaceRunLock(str(ws))
    lock_a.acquire()

    monkeypatch.setenv("OPENSQUAD_INSTANCE", "install-b")
    wu._RUN_LOCKS.clear()
    with pytest.raises(RuntimeError) as excinfo:
        wu.ensure_workspace_owner(str(ws))

    message = str(excinfo.value)
    assert "already in use" in message
    assert "install-a" in message, "the owner must be named"
    assert "OPENSQUAD_INSTANCE" in message, "the error must say how to get out of it"
    assert lock_a._fh is not None, "the owner keeps its handle — that is the liveness signal"


def test_a_dead_owners_lock_is_taken_over(tmp_path, monkeypatch):
    """A crashed install must not lock its workspace out forever."""
    ws = _ws(tmp_path, "ws")
    (ws / ".opensquad" / "instance.json").write_text(json.dumps({"slug": "gone", "pid": 999999}), encoding="utf-8")
    # No live holder: the lock file exists (as it would after a crash) but nobody holds it.
    (ws / ".opensquad" / "instance.lock").write_text("", encoding="utf-8")

    monkeypatch.setenv("OPENSQUAD_INSTANCE", "install-c")
    wu._RUN_LOCKS.clear()
    wu.ensure_workspace_owner(str(ws))  # must not raise

    owner = json.loads((ws / ".opensquad" / "instance.json").read_text(encoding="utf-8"))
    assert owner["slug"] == "install-c"
    assert owner["pid"] == os.getpid()
