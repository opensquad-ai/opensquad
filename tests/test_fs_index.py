"""Unit tests for utils/fs_index.py — git-accelerated TTL-cached tree listing."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from opensquad.utils import fs_index
from opensquad.utils.fs_index import cache_clear, list_tree


@pytest.fixture(autouse=True)
def _clear_cache():
    cache_clear()
    yield
    cache_clear()


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


NEED_GIT = pytest.mark.skipif(not _git_available(), reason="git not available")


def _make_tree(root: Path) -> None:
    (root / "README.md").write_text("# hi", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print(1)", encoding="utf-8")
    (root / "src" / "deep").mkdir()
    (root / "src" / "deep" / "nested.py").write_text("x = 1", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "pkg").mkdir()
    (root / "node_modules" / "pkg" / "index.js").write_text("y", encoding="utf-8")


class TestWalkFallback:
    def test_flat_listing(self, tmp_path: Path):
        _make_tree(tmp_path)
        result = list_tree(str(tmp_path), use_cache=False)
        assert "error" not in result
        paths = {e["path"] for e in result["entries"]}
        assert "README.md" in paths
        assert "src" in paths
        assert "src/main.py" in paths
        assert "src/deep/nested.py" in paths

    def test_skip_heavy_dirs(self, tmp_path: Path):
        _make_tree(tmp_path)
        result = list_tree(str(tmp_path), use_cache=False)
        # The heavy dir itself is listed as a skipped placeholder (UI shows it
        # greyed out) but its children are never scanned.
        heavy = [e for e in result["entries"] if e["path"] == "node_modules"]
        assert heavy and heavy[0].get("skipped") is True
        assert not any(e["path"].startswith("node_modules/") for e in result["entries"])
        assert any("node_modules" in s for s in result["skipped"])

    def test_missing_root(self, tmp_path: Path):
        result = list_tree(str(tmp_path / "nope"), use_cache=False)
        assert result.get("error") is not None
        assert result.get("status") == 404

    def test_max_entries_truncates(self, tmp_path: Path):
        for i in range(30):
            (tmp_path / f"f{i:02d}.txt").write_text("x", encoding="utf-8")
        result = list_tree(str(tmp_path), max_entries=5, use_cache=False)
        assert result["count"] <= 5
        assert result["truncated"] is True

    def test_depth_filter_and_has_more(self, tmp_path: Path):
        _make_tree(tmp_path)
        result = list_tree(str(tmp_path), max_depth=1, use_cache=False)
        paths = {e["path"] for e in result["entries"]}
        assert "README.md" in paths
        assert "src" in paths
        # Deeper entries hidden, but flagged as available
        assert "src/main.py" not in paths
        assert result["has_more"] is True
        assert result["max_depth"] == 1

    def test_symlink_loop_not_recursed(self, tmp_path: Path):
        (tmp_path / "real").mkdir()
        (tmp_path / "real" / "a.txt").write_text("x", encoding="utf-8")
        try:
            os.symlink(tmp_path, tmp_path / "real" / "loop", target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this platform")
        result = list_tree(str(tmp_path), use_cache=False)
        paths = {e["path"] for e in result["entries"]}
        # The symlink appears as a file entry (never recursed), so no infinite loop
        assert "real/loop" in paths
        assert result["count"] < 50


@NEED_GIT
class TestGitAcceleration:
    def test_git_repo_listing_respects_gitignore(self, tmp_path: Path):
        _make_tree(tmp_path)
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
        (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True, capture_output=True)

        result = list_tree(str(tmp_path), use_cache=False)
        paths = {e["path"] for e in result["entries"]}
        assert "README.md" in paths
        assert "src/main.py" in paths
        # gitignore excludes node_modules without the skip list doing anything
        assert "node_modules" not in paths

    def test_git_untracked_files_included(self, tmp_path: Path):
        _make_tree(tmp_path)
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
        (tmp_path / "new.txt").write_text("n", encoding="utf-8")
        result = list_tree(str(tmp_path), use_cache=False)
        paths = {e["path"] for e in result["entries"]}
        assert "new.txt" in paths

    def test_git_parent_dirs_materialized(self, tmp_path: Path):
        (tmp_path / "src" / "pkg").mkdir(parents=True)
        (tmp_path / "src" / "pkg" / "mod.py").write_text("x", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
        result = list_tree(str(tmp_path), use_cache=False)
        paths = {e["path"] for e in result["entries"]}
        assert "src" in paths
        assert "src/pkg" in paths
        assert "src/pkg/mod.py" in paths


class TestCache:
    def test_cache_hit_skips_rescan(self, tmp_path: Path, monkeypatch):
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        calls = {"n": 0}
        real_walk = fs_index._walk_tree

        def fake_walk(*args, **kwargs):
            calls["n"] += 1
            return real_walk(*args, **kwargs)

        monkeypatch.setattr(fs_index, "_walk_tree", fake_walk)
        list_tree(str(tmp_path))
        list_tree(str(tmp_path))
        assert calls["n"] == 1

    def test_cache_expiry(self, tmp_path: Path, monkeypatch):
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        calls = {"n": 0}
        real = fs_index._walk_tree

        def fake_walk(*args, **kwargs):
            calls["n"] += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(fs_index, "_walk_tree", fake_walk)
        list_tree(str(tmp_path))
        # Force expiry
        for key in list(fs_index._CACHE):
            exp, _ = fs_index._CACHE[key]
            fs_index._CACHE[key] = (exp - 100, fs_index._CACHE[key][1])
        list_tree(str(tmp_path))
        assert calls["n"] == 2

    def test_cache_clear(self, tmp_path: Path, monkeypatch):
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        calls = {"n": 0}
        real = fs_index._walk_tree

        def fake_walk(*args, **kwargs):
            calls["n"] += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(fs_index, "_walk_tree", fake_walk)
        list_tree(str(tmp_path))
        cache_clear()
        list_tree(str(tmp_path))
        assert calls["n"] == 2
