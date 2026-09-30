"""Tests for ``opensquad.vcs.git_service`` — the UI's git read path.

Why this module has tests of its own, rather than being covered through the
``git.*`` plugin: the plugin talks to an agent that can read an error message and
retry, while this path is clicked by a human and must answer with a *shape* the
status bar and the diff viewer can render. Three things therefore get pinned
here, each of which fails silently rather than loudly:

* the porcelain-v2 parsing (**pure**, no git needed) — a mis-split record turns a
  rename into a file called ``R100 new.txt`` and nothing else complains;
* repository discovery from a *subdirectory* — ``project_fs.list_changed`` only
  looks at the root, so the status bar would call a perfectly good repo "非 Git
  仓库" whenever the session is bound to a subfolder or a task worktree;
* ref validation — the branch name is one argv token, so ``-D`` or ``a;b`` must
  never reach git, and ``--`` must protect every pathspec.

Real repositories are created under ``tmp_path``; the tests skip when the git CLI
is missing (same rule as ``test_worktree_manager``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from opensquad.vcs import git_service as gs

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git CLI not available")

# The user's global config must not change the outcome: commit.gpgsign=true or a
# different init.defaultBranch would fail these tests for reasons that have
# nothing to do with the code under test.
_GIT_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}

# ``git_service`` spawns with ``{**os.environ, **hardening}`` — same as
# production, where the user's own identity is what a commit must carry. So the
# identity the *service's* commits use has to be in ``os.environ``, not only in
# the fixture's ``env=``: a CI runner has no ambient ``user.name``, and commit
# tests would fail there for a reason unrelated to the code under test.
for _k, _v in {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}.items():
    os.environ.setdefault(_k, _v)


def _git(cwd: Path | str, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
        env=_GIT_ENV,
    )
    return (proc.stdout or "").strip()


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _write(root, "src/app.py", "a\nb\nc\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    return root


# ---------------------------------------------------------------------------
# porcelain v2 parsing (pure)
# ---------------------------------------------------------------------------

_RAW_V2 = "\0".join(
    [
        "# branch.oid 0123456789abcdef",
        "# branch.head dev",
        "# branch.upstream origin/dev",
        "# branch.ab +2 -3",
        "# stash 4",
        "1 M. N... 100644 100644 100644 aaa bbb staged.txt",
        "1 .M N... 100644 100644 100644 aaa bbb unstaged.txt",
        "1 MM N... 100644 100644 100644 aaa bbb both.txt",
        "2 R. N... 100644 100644 100644 aaa bbb R100 renamed.txt",
        "old-name.txt",
        "u UU N... 100644 100644 100644 100644 aaa bbb ccc conflict.txt",
        "? untracked.txt",
        "",
    ]
)


class TestParseStatusV2:
    """The parser is the contract with git; every field the UI shows comes here."""

    def test_branch_headers(self):
        info = gs._parse_status_v2(_RAW_V2)
        assert info["branch"] == "dev"
        assert info["upstream"] == "origin/dev"
        assert info["ahead"] == 2 and info["behind"] == 3
        assert info["stash_count"] == 4
        assert info["head_sha"] == "0123456789abcdef"
        assert info["detached"] is False

    def test_rename_keeps_its_own_filename(self):
        """A `2` record has one extra field; splitting it wrong renames the file."""
        info = gs._parse_status_v2(_RAW_V2)
        staged = {e["path"]: e for e in info["staged"]}
        assert "renamed.txt" in staged
        assert staged["renamed.txt"]["orig_path"] == "old-name.txt"

    def test_index_and_worktree_columns_are_split(self):
        info = gs._parse_status_v2(_RAW_V2)
        assert {e["path"] for e in info["staged"]} == {"staged.txt", "both.txt", "renamed.txt"}
        assert {e["path"] for e in info["unstaged"]} == {"unstaged.txt", "both.txt"}
        assert [e["path"] for e in info["untracked"]] == ["untracked.txt"]
        assert [e["path"] for e in info["conflicts"]] == ["conflict.txt"]

    def test_total_counts_each_path_once(self):
        """`both.txt` is staged *and* unstaged but is one uncommitted file."""
        counts = gs._parse_status_v2(_RAW_V2)["counts"]
        assert counts == {"staged": 3, "unstaged": 2, "untracked": 1, "conflicts": 1, "total": 6}

    def test_detached_and_initial_repos(self):
        info = gs._parse_status_v2("\0".join(["# branch.oid (initial)", "# branch.head main", ""]))
        assert info["initial"] is True and info["head_sha"] == ""
        detached = gs._parse_status_v2("\0".join(["# branch.oid abc", "# branch.head (detached)", ""]))
        assert detached["detached"] is True and detached["branch"] is None

    def test_missing_stash_header_is_zero_not_an_error(self):
        info = gs._parse_status_v2("\0".join(["# branch.head main", ""]))
        assert info["stash_count"] == 0


# ---------------------------------------------------------------------------
# discovery + status against real repositories
# ---------------------------------------------------------------------------


class TestStatus:
    def test_non_repo_degrades_instead_of_raising(self, tmp_path: Path):
        plain = tmp_path / "plain"
        plain.mkdir()
        st = gs.status(str(plain))
        assert st["is_repo"] is False and st["repo_root"] is None
        assert st["cwd"] == str(plain)

    def test_missing_directory_is_an_error_not_a_crash(self, tmp_path: Path):
        st = gs.status(str(tmp_path / "nope"))
        assert st["is_repo"] is False and "error" in st

    def test_repo_root_found_from_a_subdirectory(self, repo: Path):
        """The session cwd may be a subfolder (or a worktree); the bar wants the root."""
        sub = repo / "src" / "deep"
        sub.mkdir(parents=True)
        st = gs.status(str(sub))
        assert st["is_repo"] is True
        assert os.path.normcase(st["repo_root"]) == os.path.normcase(str(repo))
        assert st["branch"] == "main"
        assert st["name"] == "proj"

    def test_dirty_kinds_and_counts(self, repo: Path):
        _write(repo, "src/app.py", "a\nB\nc\n")  # unstaged modification
        _write(repo, "src/new.py", "x\n")  # untracked
        _write(repo, "src/staged.py", "y\n")
        _git(repo, "add", "src/staged.py")
        st = gs.status(str(repo))
        assert [e["path"] for e in st["unstaged"]] == ["src/app.py"]
        assert [e["path"] for e in st["staged"]] == ["src/staged.py"]
        assert [e["path"] for e in st["untracked"]] == ["src/new.py"]
        assert st["counts"]["total"] == 3
        assert st["remotes"] == []
        assert st["in_progress"] == {"merge": False, "rebase": False, "cherry_pick": False, "revert": False}

    def test_ignored_files_stay_out_of_the_count(self, repo: Path):
        _write(repo, ".gitignore", "*.log\n")
        _git(repo, "add", ".gitignore")
        _git(repo, "commit", "-m", "ignore")
        _write(repo, "noise.log", "x\n")
        st = gs.status(str(repo))
        assert st["counts"]["total"] == 0

    def test_stash_count_and_in_progress_merge(self, repo: Path):
        _write(repo, "src/app.py", "a\nB\nc\n")
        _git(repo, "stash", "push", "-m", "wip")
        assert gs.status(str(repo))["stash_count"] == 1
        # A half-finished merge is what the UI's conflict banner keys off.
        (repo / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
        assert gs.status(str(repo))["in_progress"]["merge"] is True

    def test_ahead_behind_track_the_upstream(self, tmp_path: Path):
        origin = tmp_path / "origin.git"
        _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
        work = tmp_path / "work"
        _git(tmp_path, "clone", str(origin), str(work))
        # The origin is still empty, so the clone's HEAD follows the client's
        # ``init.defaultBranch`` — ``main`` on Git for Windows, ``master`` on
        # the Linux CI runner. Name it instead of assuming.
        _git(work, "checkout", "-B", "main")
        _write(work, "a.txt", "1\n")
        _git(work, "add", "-A")
        _git(work, "commit", "-m", "first")
        _git(work, "push", "-u", "origin", "main")
        _write(work, "a.txt", "1\n2\n")
        _git(work, "commit", "-am", "second")

        st = gs.status(str(work))
        assert st["upstream"] == "origin/main"
        assert (st["ahead"], st["behind"]) == (1, 0)
        assert [r["name"] for r in st["remotes"]] == ["origin"]

        # And behind, once the remote moves on (a second clone advances it —
        # a bare repo has no worktree to commit from) and we fetch.
        other = tmp_path / "other"
        _git(tmp_path, "clone", str(origin), str(other))
        _write(other, "a.txt", "1\n2\n3\n")
        _git(other, "add", "-A")
        _git(other, "commit", "-m", "remote")
        _git(other, "push", "origin", "main")
        _git(work, "fetch", "origin")
        assert (gs.status(str(work))["ahead"], gs.status(str(work))["behind"]) == (1, 1)

    def test_detached_head(self, repo: Path):
        _write(repo, "src/app.py", "a\nB\nc\n")
        _git(repo, "commit", "-am", "second")
        _git(repo, "checkout", "--detach", "HEAD~1")
        st = gs.status(str(repo))
        assert st["detached"] is True and st["branch"] is None and st["head_sha"]


# ---------------------------------------------------------------------------
# branches
# ---------------------------------------------------------------------------


class TestBranches:
    def test_current_pinned_first_then_newest_commit(self, repo: Path):
        _git(repo, "switch", "-c", "feature/old")
        _write(repo, "old.txt", "x\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "older work")
        _git(repo, "switch", "-c", "feature/new")
        _write(repo, "new.txt", "x\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "newest work")
        _git(repo, "switch", "main")

        data = gs.branches(str(repo))
        assert data["current"] == "main"
        # The checked-out branch heads the list, then the rest by commit date:
        # that is the order a picker shows (current, then recently used).
        assert [b["name"] for b in data["local"]] == ["main", "feature/new", "feature/old"]
        assert [b["current"] for b in data["local"]] == [True, False, False]
        assert data["local"][1]["subject"] == "newest work"
        # Commits written in the same second tie on timestamp, so the order also
        # has to be deterministic — hence the name tiebreak in the sort.
        assert data["local"][1]["ts"] >= data["local"][2]["ts"]

    def test_remote_rows_carry_their_remote(self, tmp_path: Path):
        origin = tmp_path / "origin.git"
        _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
        work = tmp_path / "work"
        _git(tmp_path, "clone", str(origin), str(work))
        # The origin is still empty, so the clone's HEAD follows the client's
        # ``init.defaultBranch`` — ``main`` on Git for Windows, ``master`` on
        # the Linux CI runner. Name it instead of assuming.
        _git(work, "checkout", "-B", "main")
        _write(work, "a.txt", "1\n")
        _git(work, "add", "-A")
        _git(work, "commit", "-m", "first")
        _git(work, "push", "-u", "origin", "main")
        # A clone made before the first push has no origin/HEAD; set it the way
        # a normal clone would have.
        _git(work, "remote", "set-head", "origin", "-a")

        data = gs.branches(str(work))
        assert data["default_branch"] == "main"
        assert [(r["remote"], r["name"]) for r in data["remote"]] == [("origin", "main")]
        assert data["local"][0]["upstream"] == "origin/main"

    def test_ahead_is_reported_per_branch(self, tmp_path: Path):
        origin = tmp_path / "origin.git"
        _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
        work = tmp_path / "work"
        _git(tmp_path, "clone", str(origin), str(work))
        # The origin is still empty, so the clone's HEAD follows the client's
        # ``init.defaultBranch`` — ``main`` on Git for Windows, ``master`` on
        # the Linux CI runner. Name it instead of assuming.
        _git(work, "checkout", "-B", "main")
        _write(work, "a.txt", "1\n")
        _git(work, "add", "-A")
        _git(work, "commit", "-m", "first")
        _git(work, "push", "-u", "origin", "main")
        _write(work, "a.txt", "1\n2\n")
        _git(work, "commit", "-am", "second")
        # A branch with no upstream configured must not claim ahead/behind.
        _git(work, "switch", "-c", "local-only")

        data = gs.branches(str(work))
        by_name = {b["name"]: b for b in data["local"]}
        assert by_name["main"]["ahead"] == 1 and by_name["main"]["upstream"] == "origin/main"
        assert by_name["local-only"]["ahead"] == 0 and by_name["local-only"]["upstream"] is None
        assert by_name["local-only"]["gone"] is False

    def test_non_repo_raises(self, tmp_path: Path):
        with pytest.raises(ValueError):
            gs.branches(str(tmp_path))


# ---------------------------------------------------------------------------
# ref validation (argv safety)
# ---------------------------------------------------------------------------


class TestValidateRef:
    @pytest.mark.parametrize(
        "name",
        [
            "main",
            "feature/one-1",
            "release/0.8.50",
            "fix/a.b",
            "a_b",
            # Git accepts these, so the UI must not refuse them: a stricter-than-git
            # allowlist would hide branches other tools create.
            "feature/a+b",
            "user@host/topic",
            "fix/a=1",
            "功能/登录",
        ],
    )
    def test_accepts_real_branch_names(self, name: str):
        assert gs.validate_ref(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "",  # empty
            "   ",
            "-D",  # option injection: `git switch -D` deletes
            "--force",
            "a b",
            "a;rm -rf x",
            "a$(whoami)",
            "a..b",
            "a@{1}",
            "feat.lock",
            "/lead",
            "trail/",
            "back\\slash",
            "a:b",
            "a~1",
            "a^",
            "a?",
            "a*b",
            "a[1]",
            "..",
            ".",
            "HEAD~1",
            "a\nb",
        ],
    )
    def test_rejects_anything_that_is_not_a_plain_name(self, name: str):
        with pytest.raises(ValueError):
            gs.validate_ref(name)


class TestSafeRel:
    def test_accepts_relative_and_normalizes(self, tmp_path: Path):
        repo = tmp_path / "r"
        repo.mkdir()
        assert gs.safe_rel(str(repo), "src/a.py") == "src/a.py"
        assert gs.safe_rel(str(repo), "./src/a.py") == "src/a.py"
        assert gs.safe_rel(str(repo), "src\\a.py") == "src/a.py"

    def test_accepts_an_absolute_path_inside_and_rejects_one_outside(self, tmp_path: Path):
        repo = tmp_path / "r"
        repo.mkdir()
        inside = repo / "src" / "a.py"
        assert gs.safe_rel(str(repo), str(inside)) == "src/a.py"
        outside = tmp_path / "other" / "a.py"
        assert gs.safe_rel(str(repo), str(outside)) is None

    @pytest.mark.parametrize("rel", ["../etc/passwd", "src/../../x", "..", ""])
    def test_rejects_escapes(self, tmp_path: Path, rel: str):
        repo = tmp_path / "r"
        repo.mkdir()
        assert gs.safe_rel(str(repo), rel) is None


# ---------------------------------------------------------------------------
# diffs
# ---------------------------------------------------------------------------


class TestDiffFile:
    def test_modified_file_matches_head(self, repo: Path):
        _write(repo, "src/app.py", "a\nB\nc\nd\n")
        diff = gs.diff_file(str(repo), "src/app.py")
        assert diff["status"] == "M"
        assert (diff["additions"], diff["deletions"]) == (2, 1)
        kinds = [ln["type"] for ln in diff["lines"]]
        assert "insert" in kinds and "delete" in kinds and "context" in kinds
        # Same row shape the session pane emits, so one viewer serves both.
        assert {"type", "old_lineno", "new_lineno", "text"} <= set(diff["lines"][0])

    def test_new_untracked_file_is_all_inserts(self, repo: Path):
        _write(repo, "src/new.py", "one\ntwo\n")
        diff = gs.diff_file(str(repo), "src/new.py")
        assert diff["status"] == "A"
        assert (diff["additions"], diff["deletions"]) == (2, 0)
        assert all(ln["type"] == "insert" for ln in diff["lines"])

    def test_deleted_file_is_all_deletes(self, repo: Path):
        os.remove(repo / "src" / "app.py")
        diff = gs.diff_file(str(repo), "src/app.py")
        assert diff["status"] == "D"
        assert (diff["additions"], diff["deletions"]) == (0, 3)

    def test_staged_mode_reads_the_index_not_the_disk(self, repo: Path):
        _write(repo, "src/app.py", "a\nB\nc\n")
        _git(repo, "add", "src/app.py")
        _write(repo, "src/app.py", "a\nB\nc\nD\n")  # a further, unstaged edit

        staged = gs.diff_file(str(repo), "src/app.py", mode="staged")
        worktree = gs.diff_file(str(repo), "src/app.py", mode="worktree")
        assert (staged["additions"], staged["deletions"]) == (1, 1)
        assert (worktree["additions"], worktree["deletions"]) == (2, 1)

    def test_binary_is_flagged_oversized_without_lines(self, repo: Path):
        (repo / "blob.bin").write_bytes(b"\x00\x01\x02\xff")
        diff = gs.diff_file(str(repo), "blob.bin")
        assert diff["oversized"] is True and diff["lines"] == []

    def test_path_outside_the_repo_is_refused(self, repo: Path, tmp_path: Path):
        diff = gs.diff_file(str(repo), "../secret.txt")
        assert diff["status"] == 400 and "outside" in diff["error"]

    def test_non_repo_is_refused(self, tmp_path: Path):
        diff = gs.diff_file(str(tmp_path), "a.txt")
        assert diff["status"] == 400


# ---------------------------------------------------------------------------
# locking + key determinism
# ---------------------------------------------------------------------------


class TestRepoLock:
    def test_key_is_stable_and_per_repo(self, tmp_path: Path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        assert gs.repo_key(str(a)) == gs.repo_key(str(a) + os.sep)
        assert gs.repo_key(str(a)) != gs.repo_key(str(b))
        assert gs.repo_key(str(a)).startswith("git_repo_")

    def test_second_holder_is_refused_not_queued(self, repo: Path):
        """Contention must surface as a locked repo, not a hanging request."""
        from opensquad.distributed_lock import LockTimeoutError

        with gs.repo_lock(str(repo), timeout=2.0):
            with pytest.raises(LockTimeoutError):
                with gs.repo_lock(str(repo), timeout=0.2):
                    pass

    def test_lock_contention_detection_matches_git_wording(self):
        assert gs.is_lock_contention("fatal: Unable to create '.git/index.lock': File exists.")
        assert gs.is_lock_contention("Another git process seems to be running in this repository")
        assert not gs.is_lock_contention("fatal: not a git repository")


# ---------------------------------------------------------------------------
# write operations
# ---------------------------------------------------------------------------


def _stage_all(repo: Path) -> None:
    _git(repo, "add", "-A")


class TestInitRepo:
    def test_initialises_an_unversioned_folder(self, tmp_path: Path):
        folder = tmp_path / "plain"
        folder.mkdir()
        res = gs.init_repo(str(folder))
        assert res["ok"] is True and res["branch"] == "main"
        # Deterministic branch, regardless of the machine's init.defaultBranch.
        # (An unborn branch has no revision yet, so ask for the symref.)
        assert _git(folder, "symbolic-ref", "--short", "HEAD") == "main"
        assert gs.status(str(folder))["is_repo"] is True

    def test_refuses_an_existing_repo_and_a_missing_folder(self, repo: Path, tmp_path: Path):
        assert gs.init_repo(str(repo))["code"] == "already_repo"
        assert gs.init_repo(str(tmp_path / "gone"))["code"] == "not_a_directory"


class TestCheckout:
    def test_switch_to_an_existing_branch(self, repo: Path):
        _git(repo, "branch", "other")
        res = gs.checkout(str(repo), "other")
        assert res == {"ok": True, "branch": "other", "created": False, "stashed": False}
        assert gs.status(str(repo))["branch"] == "other"

    def test_dirty_worktree_is_refused_until_stashing_is_asked_for(self, repo: Path):
        _git(repo, "branch", "other")
        _write(repo, "src/app.py", "a\nB\nc\n")
        refused = gs.checkout(str(repo), "other")
        assert refused["code"] == "dirty_worktree"
        assert refused["paths"] == ["src/app.py"]
        assert gs.status(str(repo))["branch"] == "main"

        ok = gs.checkout(str(repo), "other", stash_dirty=True)
        assert ok["ok"] is True and ok["stashed"] is True
        assert gs.status(str(repo))["branch"] == "other"
        # The work is parked in a stash the user can get back, and reported.
        assert gs.status(str(repo))["stash_count"] == 1
        assert gs.status(str(repo))["counts"]["total"] == 0

    def test_create_branch_from_a_base(self, repo: Path):
        res = gs.checkout(str(repo), "feature/two", create=True, base="main")
        assert res["ok"] is True and res["created"] is True
        assert gs.status(str(repo))["branch"] == "feature/two"

    def test_invalid_ref_is_refused_without_touching_the_repo(self, repo: Path):
        res = gs.checkout(str(repo), "-D")
        assert res["code"] == "bad_ref"
        assert gs.status(str(repo))["branch"] == "main"

    def test_failed_switch_puts_a_taken_stash_back(self, repo: Path):
        """A stash taken for a switch that then fails must not be left behind."""
        _write(repo, "src/app.py", "a\nB\nc\n")
        res = gs.checkout(str(repo), "does-not-exist", stash_dirty=True)
        assert res["code"] == "checkout_failed" and res["unstashed"] is True
        st = gs.status(str(repo))
        assert st["stash_count"] == 0
        assert st["counts"]["unstaged"] == 1

    def test_non_repo_is_a_code_not_an_exception(self, tmp_path: Path):
        assert gs.checkout(str(tmp_path), "main")["code"] == "not_a_repo"

    def test_locked_repo_answers_locked(self, repo: Path):
        _git(repo, "branch", "other")
        with gs.repo_lock(str(repo), timeout=2.0):
            res = gs.checkout(str(repo), "other")
        assert res["code"] == "locked"


class TestBranchAdmin:
    def test_delete_refuses_the_checked_out_branch(self, repo: Path):
        res = gs.delete_branch(str(repo), "main")
        assert res["code"] == "current_branch"

    def test_delete_unmerged_needs_the_force_flag(self, repo: Path):
        _git(repo, "switch", "-c", "wip")
        _write(repo, "w.txt", "x\n")
        _stage_all(repo)
        _git(repo, "commit", "-m", "unmerged work")
        _git(repo, "switch", "main")

        refused = gs.delete_branch(str(repo), "wip")
        assert refused["code"] == "not_merged"
        assert gs.delete_branch(str(repo), "wip", force=True)["ok"] is True
        assert [b["name"] for b in gs.branches(str(repo))["local"]] == ["main"]


class TestStaging:
    def test_stage_and_unstage_round_trip(self, repo: Path):
        _write(repo, "src/app.py", "a\nB\nc\n")
        assert gs.stage(str(repo), ["src/app.py"])["ok"] is True
        st = gs.status(str(repo))
        assert [e["path"] for e in st["staged"]] == ["src/app.py"]
        assert st["counts"]["unstaged"] == 0

        assert gs.unstage(str(repo), ["src/app.py"])["ok"] is True
        st = gs.status(str(repo))
        assert st["counts"]["staged"] == 0 and st["counts"]["unstaged"] == 1

    def test_unstage_works_before_the_first_commit(self, tmp_path: Path):
        root = tmp_path / "fresh"
        root.mkdir()
        _git(root, "init", "-b", "main")
        _write(root, "a.txt", "1\n")
        assert gs.stage(str(root), ["a.txt"])["ok"] is True
        assert gs.unstage(str(root), ["a.txt"])["ok"] is True
        assert gs.status(str(root))["counts"] == {
            "staged": 0,
            "unstaged": 0,
            "untracked": 1,
            "conflicts": 0,
            "total": 1,
        }

    def test_paths_outside_the_repo_are_refused_as_a_batch(self, repo: Path):
        _write(repo, "src/app.py", "a\nB\nc\n")
        res = gs.stage(str(repo), ["src/app.py", "../outside.txt"])
        assert res["code"] == "bad_path"
        assert res["error"].startswith("Path outside the repository")
        # Nothing was staged: the batch is all-or-nothing.
        assert gs.status(str(repo))["counts"]["staged"] == 0

    def test_empty_path_list_is_refused(self, repo: Path):
        assert gs.stage(str(repo), [])["code"] == "no_paths"


class TestDiscard:
    def test_restores_a_tracked_file_from_head(self, repo: Path):
        _write(repo, "src/app.py", "a\nB\nc\n")
        assert gs.discard(str(repo), ["src/app.py"])["ok"] is True
        assert (repo / "src" / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n") == "a\nb\nc\n"
        assert gs.status(str(repo))["counts"]["total"] == 0

    def test_untracked_file_needs_an_explicit_confirmation(self, repo: Path):
        _write(repo, "src/new.py", "x\n")
        refused = gs.discard(str(repo), ["src/new.py"])
        assert refused["code"] == "untracked_needs_confirmation"
        assert refused["paths"] == ["src/new.py"]
        assert (repo / "src" / "new.py").exists()

        ok = gs.discard(str(repo), ["src/new.py"], remove_untracked=True)
        assert ok["ok"] is True and ok["removed"] == ["src/new.py"]
        assert not (repo / "src" / "new.py").exists()

    def test_discard_also_drops_the_staged_copy(self, repo: Path):
        _write(repo, "src/app.py", "a\nB\nc\n")
        _stage_all(repo)
        assert gs.discard(str(repo), ["src/app.py"])["ok"] is True
        assert gs.status(str(repo))["counts"]["total"] == 0


class TestCommit:
    def test_commits_only_what_is_staged(self, repo: Path):
        _write(repo, "src/app.py", "a\nB\nc\n")
        _write(repo, "src/other.py", "z\n")
        gs.stage(str(repo), ["src/app.py"])

        res = gs.commit(str(repo), "fix: app", "why it changed")
        assert res["ok"] is True and res["subject"] == "fix: app"
        # The subject line is what git records; the body follows after a blank line.
        assert _git(repo, "log", "-1", "--format=%s") == "fix: app"
        assert _git(repo, "log", "-1", "--format=%b") == "why it changed"
        assert _git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["src/app.py"]
        # The other file is still sitting there uncommitted.
        assert gs.status(str(repo))["counts"]["total"] == 1

    def test_title_is_required_and_an_empty_index_is_refused(self, repo: Path):
        _write(repo, "src/app.py", "a\nB\nc\n")
        assert gs.commit(str(repo), "   ")["code"] == "empty_message"
        assert gs.commit(str(repo), "fix: x")["code"] == "nothing_staged"
        assert _git(repo, "log", "--oneline").count("\n") == 0  # still one commit

    def test_a_conflicting_name_in_the_hook_is_not_swallowed(self, repo: Path):
        """Hooks run: a failing pre-commit must fail the commit, not be bypassed."""
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        os.chmod(hook, 0o755)
        _write(repo, "src/app.py", "a\nB\nc\n")
        _stage_all(repo)
        res = gs.commit(str(repo), "fix: blocked")
        # Windows git may ignore the shell hook; on POSIX it must block.
        if os.name != "nt":
            assert res["code"] == "commit_failed"
        assert _git(repo, "log", "-1", "--format=%s") in ("fix: blocked", "init")


class TestUndoLastCommit:
    def test_undoes_an_unpushed_commit_and_keeps_it_staged(self, repo: Path):
        _write(repo, "src/app.py", "a\nB\nc\n")
        _stage_all(repo)
        gs.commit(str(repo), "second")
        assert _git(repo, "log", "-1", "--format=%s") == "second"

        res = gs.undo_last_commit(str(repo))
        assert res["ok"] is True
        assert _git(repo, "log", "-1", "--format=%s") == "init"
        st = gs.status(str(repo))
        assert st["counts"]["staged"] == 1  # --soft: the change is back in the index

    def test_refuses_once_the_commit_is_on_a_remote(self, tmp_path: Path):
        origin = tmp_path / "origin.git"
        _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
        work = tmp_path / "work"
        _git(tmp_path, "clone", str(origin), str(work))
        # The origin is still empty, so the clone's HEAD follows the client's
        # ``init.defaultBranch`` — ``main`` on Git for Windows, ``master`` on
        # the Linux CI runner. Name it instead of assuming.
        _git(work, "checkout", "-B", "main")
        _write(work, "a.txt", "1\n")
        _stage_all(work)
        gs.commit(str(work), "first")
        _git(work, "push", "-u", "origin", "main")

        # Unpushed → undo is allowed (the whole point of the feature).
        _write(work, "a.txt", "1\n2\n")
        _stage_all(work)
        gs.commit(str(work), "not yet shared")
        assert gs.undo_last_commit(str(work))["ok"] is True

        # Published → rewriting it would need a force push, so refuse.
        _write(work, "a.txt", "1\n2\n3\n")
        _stage_all(work)
        gs.commit(str(work), "published")
        _git(work, "push")
        res = gs.undo_last_commit(str(work))
        assert res["code"] == "already_pushed"
        assert _git(work, "log", "-1", "--format=%s") == "published"

    def test_refuses_the_root_commit(self, repo: Path):
        res = gs.undo_last_commit(str(repo))
        assert res["code"] == "no_parent"


# ---------------------------------------------------------------------------
# sync: fetch / pull / push
# ---------------------------------------------------------------------------


@pytest.fixture()
def remote_pair(tmp_path: Path) -> tuple[Path, Path]:
    """``(origin, work)`` — a bare origin and a clone of it, both on ``main``."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", str(origin), str(work))
    # The origin is still empty, so the clone's HEAD follows the client's
    # ``init.defaultBranch`` — ``main`` on Git for Windows, ``master`` on the
    # Linux CI runner. Name it instead of assuming.
    _git(work, "checkout", "-B", "main")
    _write(work, "a.txt", "1\n")
    _stage_all(work)
    gs.commit(str(work), "first")
    _git(work, "push", "-u", "origin", "main")
    return origin, work


def _advance_remote(tmp_path: Path, origin: Path, name: str = "other") -> Path:
    """Push a new commit to *origin* from a second clone, returning that clone."""
    peer = tmp_path / name
    if not peer.exists():
        _git(tmp_path, "clone", str(origin), str(peer))
    _write(peer, "a.txt", f"1\n{peer.name}\n")
    _stage_all(peer)
    gs.commit(str(peer), f"from {peer.name}")
    _git(peer, "push")
    return peer


class TestFetch:
    def test_fetch_moves_the_remote_tracking_ref(self, remote_pair: tuple[Path, Path], tmp_path: Path):
        origin, work = remote_pair
        _advance_remote(tmp_path, origin)

        res = gs.fetch(str(work))
        assert res["ok"] is True and res["remote"] == "origin"
        assert res["behind"] == 1
        # Fetch never touches the worktree or the index.
        assert gs.status(str(work))["counts"]["total"] == 0
        assert (work / "a.txt").read_text(encoding="utf-8").replace("\r\n", "\n") == "1\n"

    def test_fetch_without_a_remote_is_a_code(self, repo: Path):
        assert gs.fetch(str(repo))["code"] == "no_remote"

    def test_unknown_remote_is_refused(self, remote_pair: tuple[Path, Path]):
        _origin, work = remote_pair
        res = gs.fetch(str(work), remote="upstream")
        assert res["code"] == "no_remote"


class TestPull:
    def test_fast_forward(self, remote_pair: tuple[Path, Path], tmp_path: Path):
        origin, work = remote_pair
        _advance_remote(tmp_path, origin)

        res = gs.pull(str(work))
        assert res["ok"] is True and res["mode"] == "fast_forward"
        assert (work / "a.txt").read_text(encoding="utf-8").replace("\r\n", "\n") == "1\nother\n"

    def test_up_to_date_when_nothing_moved(self, remote_pair: tuple[Path, Path]):
        _origin, work = remote_pair
        res = gs.pull(str(work))
        assert res["ok"] is True and res["mode"] == "up_to_date"

    def test_divergence_is_merged_not_rebased(self, remote_pair: tuple[Path, Path], tmp_path: Path):
        origin, work = remote_pair
        _advance_remote(tmp_path, origin)
        _write(work, "local.txt", "mine\n")
        _stage_all(work)
        gs.commit(str(work), "local work")

        res = gs.pull(str(work))
        assert res["ok"] is True and res["mode"] == "merge"
        # A merge commit has two parents — the pushed history was not rewritten.
        parents = _git(work, "rev-list", "--parents", "-1", "HEAD").split()
        assert len(parents) == 3
        assert not gs.status(str(work))["in_progress"]["merge"]

    def test_local_edits_are_refused_without_autostash(self, remote_pair: tuple[Path, Path], tmp_path: Path):
        origin, work = remote_pair
        _advance_remote(tmp_path, origin)
        _write(work, "a.txt", "1\nuncommitted\n")

        refused = gs.pull(str(work), autostash=False)
        assert refused["code"] == "dirty_worktree"
        assert refused["paths"] == ["a.txt"]
        assert (work / "a.txt").read_text(encoding="utf-8").replace("\r\n", "\n") == "1\nuncommitted\n"

    def test_autostash_keeps_a_non_conflicting_local_edit(self, remote_pair: tuple[Path, Path], tmp_path: Path):
        """A dirty file the pull also touches is exactly what autostash is for.

        Git refuses a pull that would overwrite modified files, so this only
        succeeds because ``--autostash`` parks the edit and replays it: the
        remote changed line 1, our uncommitted edit is line 3.
        """
        origin, work = remote_pair
        _write(work, "a.txt", "1\n2\n3\n")
        _stage_all(work)
        gs.commit(str(work), "expand")
        _git(work, "push")

        peer = tmp_path / "peer"
        _git(tmp_path, "clone", str(origin), str(peer))
        _write(peer, "a.txt", "1x\n2\n3\n")
        _stage_all(peer)
        gs.commit(str(peer), "remote line 1")
        _git(peer, "push")

        _write(work, "a.txt", "1\n2\n3x\n")  # uncommitted, same file, other region
        res = gs.pull(str(work))
        assert res["ok"] is True and res["mode"] == "fast_forward"
        assert (work / "a.txt").read_text(encoding="utf-8").replace("\r\n", "\n") == "1x\n2\n3x\n"
        # The autostash is not left behind.
        assert gs.status(str(work))["stash_count"] == 0

    def test_autostash_conflict_is_reported_as_such(self, remote_pair: tuple[Path, Path], tmp_path: Path):
        """Replaying the autostash can itself conflict — that is not a merge conflict.

        Git keeps the autostash entry in that case (so nothing is lost), and the
        worktree carries the usual conflict markers; the UI is told ``via:
        autostash`` because ``merge --abort`` is not the way out of it.
        """
        origin, work = remote_pair
        _advance_remote(tmp_path, origin)  # the remote rewrites a.txt's last line
        _write(work, "a.txt", "1\nother\nuncommitted\n")  # local edit over the same region

        res = gs.pull(str(work))
        assert res["code"] == "conflicts"
        assert res["via"] == "autostash"
        assert res["conflicts"] == ["a.txt"]
        assert gs.status(str(work))["stash_count"] == 1  # git kept it
        assert "<<<<<<<" in (work / "a.txt").read_text(encoding="utf-8")

    def test_a_pre_existing_conflict_is_reported_as_conflicts(self, remote_pair: tuple[Path, Path], tmp_path: Path):
        """State decides, not git's wording: an unfinished merge is still `conflicts`.

        Git answers a pull attempted mid-merge with "You have not concluded your
        merge", which classifies as a generic failure — but the conflict is the
        fact the UI must act on.
        """
        origin, work = remote_pair
        peer = _advance_remote(tmp_path, origin)
        _write(peer, "a.txt", "1\nfrom-the-other-side\n")
        _stage_all(peer)
        gs.commit(str(peer), "conflicting remote edit")
        _git(peer, "push")
        gs.fetch(str(work))
        _write(work, "a.txt", "1\nfrom-mine\n")
        _stage_all(work)
        gs.commit(str(work), "conflicting local edit")
        _git(work, "merge", "origin/main", check=False)  # leave the tree conflicted

        res = gs.pull(str(work))
        assert res["code"] == "conflicts"
        assert res["conflicts"] == ["a.txt"]
        assert res["via"] == "merge"

    def test_conflict_is_reported_and_leaves_the_merge_in_progress(
        self, remote_pair: tuple[Path, Path], tmp_path: Path
    ):
        origin, work = remote_pair
        peer = _advance_remote(tmp_path, origin)  # changes a.txt the same way we will
        _write(peer, "a.txt", "1\nfrom-the-other-side\n")
        _stage_all(peer)
        gs.commit(str(peer), "conflicting remote edit")
        _git(peer, "push")
        _write(work, "a.txt", "1\nfrom-mine\n")
        _stage_all(work)
        gs.commit(str(work), "conflicting local edit")

        res = gs.pull(str(work))
        assert res["code"] == "conflicts"
        assert res["conflicts"] == ["a.txt"]
        st = gs.status(str(work))
        assert st["in_progress"]["merge"] is True

        # And the way out leaves a clean worktree on the pre-pull commit.
        assert gs.abort_merge(str(work))["ok"] is True
        assert gs.status(str(work))["in_progress"]["merge"] is False
        assert (work / "a.txt").read_text(encoding="utf-8").replace("\r\n", "\n") == "1\nfrom-mine\n"


class TestPush:
    def test_pushes_a_new_commit_and_reports_clean(self, remote_pair: tuple[Path, Path]):
        _origin, work = remote_pair
        _write(work, "b.txt", "x\n")
        _stage_all(work)
        gs.commit(str(work), "second")

        res = gs.push(str(work))
        assert res["ok"] is True and res["ahead"] == 0
        assert _git(work, "rev-parse", "origin/main") == _git(work, "rev-parse", "HEAD")

    def test_publish_without_upstream(self, remote_pair: tuple[Path, Path]):
        _origin, work = remote_pair
        _git(work, "switch", "-c", "feature/publish")
        _write(work, "c.txt", "x\n")
        _stage_all(work)
        gs.commit(str(work), "on a new branch")

        refused = gs.push(str(work))
        assert refused["code"] == "no_upstream" and refused["hint"] == "set_upstream"
        assert gs.push(str(work), set_upstream=True)["ok"] is True
        assert gs.status(str(work))["upstream"] == "origin/feature/publish"

    def test_rejected_push_points_at_pulling_first(self, remote_pair: tuple[Path, Path], tmp_path: Path):
        origin, work = remote_pair
        _advance_remote(tmp_path, origin)
        _write(work, "local.txt", "mine\n")
        _stage_all(work)
        gs.commit(str(work), "local")

        res = gs.push(str(work))
        assert res["code"] == "non_fast_forward"
        assert "pull" in res["hint"].lower()
        # The commit is still there, unpushed.
        assert gs.status(str(work))["ahead"] == 1

    def test_force_is_only_available_as_force_with_lease(self, remote_pair: tuple[Path, Path]):
        _origin, work = remote_pair
        assert "force" not in gs.push.__code__.co_varnames  # no plain force parameter at all
        _write(work, "b.txt", "x\n")
        _stage_all(work)
        gs.commit(str(work), "second")
        assert gs.push(str(work), force_with_lease=True)["ok"] is True

    def test_force_with_lease_refuses_to_clobber_a_moved_remote(self, remote_pair: tuple[Path, Path], tmp_path: Path):
        origin, work = remote_pair
        peer = _advance_remote(tmp_path, origin)  # the remote moves, we never fetch
        _write(work, "b.txt", "x\n")
        _stage_all(work)
        gs.commit(str(work), "local")
        _git(work, "reset", "--hard", "HEAD~1")  # our own history is now rewritten

        res = gs.push(str(work), force_with_lease=True)
        assert res["code"] in ("stale_lease", "non_fast_forward")
        # The peer's commit is still the remote's tip: nothing was clobbered.
        assert _git(origin, "rev-parse", "main") == _git(peer, "rev-parse", "HEAD")


class TestSyncClassification:
    """Git's wording is the input; the UI's next action is the output."""

    @pytest.mark.parametrize(
        ("stderr", "code"),
        [
            (
                "! [rejected]        main -> main (non-fast-forward)\nerror: failed to push some refs",
                "non_fast_forward",
            ),
            ("! [rejected]        main -> main (fetch first)", "non_fast_forward"),
            ("fatal: could not read Username for 'https://example.com': terminal prompts disabled", "auth_failed"),
            ("remote: Invalid username or password.", "auth_failed"),
            ("fatal: Authentication failed for 'https://example.com/x.git/'", "auth_failed"),
            ("error: The requested URL returned error: 403", "auth_failed"),
            ("fatal: The current branch x has no upstream branch.", "no_upstream"),
            ("fatal: repository 'https://example.com/x.git/' not found", "no_remote"),
            ("fatal: unable to access 'https://x/y.git/': Could not resolve host: x", "network"),
            ("! [rejected] main -> main (stale info)", "stale_lease"),
            ("fatal: something else entirely", "sync_failed"),
        ],
    )
    def test_codes(self, stderr: str, code: str):
        assert gs.classify_sync_error(stderr)[0] == code


class TestCredentialHandling:
    def test_a_configured_token_is_injected_for_an_https_remote(self, remote_pair: tuple[Path, Path], monkeypatch):
        """Without the injection, an HTTPS push has nowhere to get a password.

        The credential is added to the URL for this one call, so nothing is
        written back into the repository config.
        """
        _origin, work = remote_pair
        _git(work, "remote", "set-url", "origin", "https://host/x.git")
        monkeypatch.setattr(gs, "credentials", lambda: {"username": "u", "access_token": "tok"})

        target, token = gs._sync_target(str(work), "origin")
        assert target == "https://u:tok@host/x.git"
        assert token == "tok"

    def test_no_token_leaves_the_remote_alone(self, remote_pair: tuple[Path, Path], monkeypatch):
        """With no configured token, git's own credential helper must still work."""
        _origin, work = remote_pair
        _git(work, "remote", "set-url", "origin", "https://host/x.git")
        monkeypatch.setattr(gs, "credentials", lambda: {"username": "", "access_token": ""})
        assert gs._sync_target(str(work), "origin") == ("origin", "")

        _git(work, "remote", "set-url", "origin", "git@host:x.git")
        monkeypatch.setattr(gs, "credentials", lambda: {"username": "u", "access_token": "tok"})
        # An ssh remote has no userinfo field, so the URL is passed through.
        assert gs._sync_target(str(work), "origin")[0] == "origin"

    def test_token_is_injected_into_an_https_url_only(self):
        assert gs._authed_url("https://host/x.git", username="u", token="tok") == "https://u:tok@host/x.git"
        assert gs._authed_url("https://host:8443/x.git", username="", token="tok") == (
            "https://oauth2:tok@host:8443/x.git"
        )
        # A file:// or ssh remote has no userinfo to hold a token.
        assert gs._authed_url("git@host:x.git", username="u", token="tok") == "git@host:x.git"
        assert gs._authed_url("https://host/x.git", username="u", token="") == "https://host/x.git"

    def test_masking_covers_git_echoed_urls(self):
        text = "fatal: unable to access 'https://u:s3cr3t@host/x.git/': 403"
        masked = gs._mask(text, "s3cr3t")
        assert "s3cr3t" not in masked and "***" in masked

    def test_masking_covers_the_url_encoded_form(self):
        assert "p%40ss" not in gs._mask("https://u:p%40ss@h/x.git failed", "p@ss")

    def test_a_failed_sync_never_returns_the_token(self, remote_pair: tuple[Path, Path], monkeypatch):
        """The failing path is the one that matters: git echoes the URL in errors."""
        _origin, work = remote_pair
        monkeypatch.setattr(gs, "credentials", lambda: {"username": "u", "access_token": "s3cr3t"})
        _git(work, "remote", "set-url", "origin", "https://example.invalid/x.git")
        res = gs.push(str(work))
        assert res["ok"] is False
        assert "s3cr3t" not in res["error"]

    def test_credentials_never_land_in_the_repo_config(self, remote_pair: tuple[Path, Path], monkeypatch):
        _origin, work = remote_pair
        monkeypatch.setattr(gs, "credentials", lambda: {"username": "u", "access_token": "s3cr3t"})
        _git(work, "remote", "set-url", "origin", "https://example.invalid/x.git")
        _write(work, "b.txt", "x\n")
        _stage_all(work)
        gs.commit(str(work), "second")
        gs.push(str(work))

        config = (work / ".git" / "config").read_text(encoding="utf-8")
        assert "s3cr3t" not in config
        # The stored remote URL is the one the user configured.
        assert _git(work, "remote", "get-url", "origin") == "https://example.invalid/x.git"
