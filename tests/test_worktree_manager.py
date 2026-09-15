"""Tests for opensquad.workspace.worktree_manager (M1).

Uses a real temporary git repository — these are integration-style tests and
require the ``git`` CLI to be on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from opensquad.workspace import worktree_manager as wm

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git CLI not available")


def _git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GIT_PAGER": "cat", "PAGER": "cat"},
    )
    return (proc.stdout or "").strip()


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _git(str(root), "init", "-b", "main")
    _git(str(root), "config", "user.email", "t@t")
    _git(str(root), "config", "user.name", "t")
    (root / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(str(root), "add", ".")
    _git(str(root), "commit", "-m", "init")
    return str(root)


class TestCreate:
    def test_creates_worktree_with_branch(self, repo):
        mgr = wm.WorktreeManager(repo)
        meta = mgr.create("task-001")
        assert os.path.isdir(meta["worktree_path"])
        assert meta["branch"] == "os-task/task-001"
        assert meta["base_ref"] == "main"
        # worktree shares the same content
        assert os.path.isfile(os.path.join(meta["worktree_path"], "a.txt"))

    def test_excludes_worktrees_dir(self, repo):
        wm.WorktreeManager(repo)
        info_exclude = os.path.join(repo, ".git", "info", "exclude")
        assert os.path.isfile(info_exclude)
        assert "/.os-worktrees/" in open(info_exclude, encoding="utf-8").read()

    def test_duplicate_task_rejected(self, repo):
        mgr = wm.WorktreeManager(repo)
        mgr.create("task-001")
        with pytest.raises(FileExistsError):
            mgr.create("task-001")

    def test_invalid_task_id_rejected(self, repo):
        mgr = wm.WorktreeManager(repo)
        with pytest.raises(ValueError):
            mgr.create("!!!")  # sanitizes to empty string → invalid

    def test_task_id_sanitized(self, repo):
        mgr = wm.WorktreeManager(repo)
        meta = mgr.create("bad id with spaces!!")
        assert meta["task_id"] == "bad-id-with-spaces"

    def test_non_git_returns_none_manager(self, tmp_path):
        assert wm.manager_for(str(tmp_path)) is None


class TestDiffReport:
    def test_reports_changes(self, repo):
        mgr = wm.WorktreeManager(repo)
        meta = mgr.create("task-002")
        wt = meta["worktree_path"]
        with open(os.path.join(wt, "a.txt"), "a", encoding="utf-8") as fh:
            fh.write("more\n")
        with open(os.path.join(wt, "b.txt"), "w", encoding="utf-8") as fh:
            fh.write("new file\n")
        report = mgr.diff_report("task-002")
        assert report["status"] == "ok"
        assert report["insertions"] >= 2
        paths = {f["path"] for f in report["files"]}
        assert "a.txt" in paths and "b.txt" in paths

    def test_unknown_task_errors(self, repo):
        mgr = wm.WorktreeManager(repo)
        assert mgr.diff_report("nope")["status"] == "error"


class TestMergeDiscard:
    def test_merge_squash_into_base(self, repo):
        mgr = wm.WorktreeManager(repo)
        meta = mgr.create("task-003")
        wt = meta["worktree_path"]
        with open(os.path.join(wt, "a.txt"), "a", encoding="utf-8") as fh:
            fh.write("merged line\n")
        result = mgr.merge("task-003")
        assert result["status"] == "ok"
        content = open(os.path.join(repo, "a.txt"), encoding="utf-8").read()
        assert "merged line" in content
        # worktree artifacts cleaned
        assert mgr.get("task-003") is None
        assert not os.path.exists(wt)

    def test_discard_removes_worktree_and_branch(self, repo):
        mgr = wm.WorktreeManager(repo)
        meta = mgr.create("task-004")
        wt = meta["worktree_path"]
        result = mgr.discard("task-004")
        assert result["status"] == "ok"
        assert not os.path.exists(wt)
        branches = _git(repo, "branch", "--list", "os-task/*")
        assert "task-004" not in branches


class TestPrepareTaskWorkspace:
    def test_returns_worktree_path_for_git_repo(self, repo):
        out = wm.prepare_task_workspace(repo, "task-005")
        assert out["worktree"] is not None
        assert os.path.isdir(out["path"])

    def test_falls_back_to_project_for_non_git(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        out = wm.prepare_task_workspace(str(plain), "task-006")
        assert out["worktree"] is None
        assert out["path"] == str(plain)

    def test_new_task_id_format(self):
        tid = wm.new_task_id()
        assert tid.startswith("t") and len(tid) == 13
