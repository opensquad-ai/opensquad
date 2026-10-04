"""A process must be able to say which code it runs, and whether that code is behind the files.

The failure this guards against happened for real and repeatedly: fixes landed, the gateway and the
agents kept running the build they had started with, and both sides went on diagnosing a bug that
was already fixed. Nothing in the system could tell them apart, so the honest fix is to make a stale
process say so — at startup, in the health payload, and loudly when its source files are newer than
it is.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad import build_info  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_stamps():
    """The stamps are cached for the life of a process, so a test that patches them must start clean."""
    for name in ("commit", "commit_time", "version", "describe"):
        getattr(build_info, name).cache_clear()
    yield
    for name in ("commit", "commit_time", "version", "describe"):
        getattr(build_info, name).cache_clear()


def _tree(tmp_path: Path, files: dict[str, float]) -> Path:
    root = tmp_path / "pkg"
    (root / "sub").mkdir(parents=True, exist_ok=True)
    now = time.time()
    for name, age in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
        stamp = now - age
        os.utime(path, (stamp, stamp))
    return root


def test_it_names_the_version_and_the_source_tree():
    described = build_info.describe()

    assert "opensquad" in described
    assert f"src={build_info.SOURCE_ROOT}" in described


def test_it_survives_having_no_git_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(build_info, "_git", lambda args, cwd: "")
    build_info.commit.cache_clear()
    build_info.describe.cache_clear()

    assert build_info.commit() == ""
    assert "opensquad" in build_info.describe()


def test_a_file_newer_than_the_process_makes_it_stale(tmp_path):
    root = _tree(tmp_path, {"old.py": 3600, "fresh.py": 5})

    said = build_info.staleness(started_at=time.time() - 600, root=root)

    assert "fresh.py" in said
    assert "running the older code" in said


def test_a_process_newer_than_the_files_is_not_stale(tmp_path):
    root = _tree(tmp_path, {"old.py": 3600, "older.py": 7200})

    assert build_info.staleness(started_at=time.time() - 60, root=root) == ""


def test_it_reports_the_newest_change_and_ignores_noise_directories(tmp_path):
    root = _tree(tmp_path, {"a.py": 3600})
    ignored = root / "__pycache__" / "junk.py"
    ignored.parent.mkdir(parents=True, exist_ok=True)
    ignored.write_text("x = 1\n", encoding="utf-8")

    newest = build_info.newest_source_change(root=root)

    assert newest is not None
    assert newest[0].endswith("a.py"), "build output and caches are not source"


def test_the_health_payload_carries_the_build_and_the_stale_flag():
    main = (_SRC / "opensquad" / "gateway" / "backend" / "app" / "main.py").read_text(encoding="utf-8")

    assert '"build"' in main
    assert "build_info" in main


def test_snapshot_serializes(tmp_path):
    payload = build_info.snapshot()

    assert json.loads(json.dumps(payload))["source_root"] == str(build_info.SOURCE_ROOT)
    assert isinstance(payload["stale"], str)
