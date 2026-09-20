"""
opensquad/workspace/worktree_manager.py — Task worktree isolation (M1).

Each parallel task (M2 TaskScheduler / scheduled-task fire / delegated goal
milestone) that runs against a git repository gets its own `git worktree` so
its edits never collide with the focused session or other tasks. Sessions
spawned for such a task have their project cwd pointed at the worktree via
the per-session cwd override that already flows through
``opensquad.utils.path_utils.get_workspace_root()`` — the existing tool
layer (filesystem/system) automatically constrains reads/writes/shell
execution to the worktree, no per-tool changes required.

Layout::

    <repo>/.os-worktrees/<task_id>/     — working tree
    <repo>/.os-worktrees/_refs/<task_id>.json — base ref & branch metadata

The ``.os-worktrees`` directory is always inside the repository (git refuses
worktrees whose path is tracked or under an existing one), is added to
``.git/info/exclude`` at manager init, and is filtered out of the frontend
"Changes" surface by the session-changeset layer (its root IS the worktree,
so anything outside it is already rejected by ``_reject_outside_session_project``).

Merge decisions are deliberately NOT automatic: when a task finishes we
publish a change report (``diff_report``) and the user chooses
merge / keep-branch / discard via the task panel (M2) or the REST API below.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid

from opensquad.proc_text import utf8_text_kwargs
from opensquad.system_config import syscfg

logger = logging.getLogger(__name__)

TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _sanitize_task_id(task_id: str) -> str:
    tid = re.sub(r"[^A-Za-z0-9_-]+", "-", str(task_id or "").strip()).strip("-")
    if not TASK_ID_RE.match(tid):
        raise ValueError(f"Invalid task_id: {task_id!r}")
    return tid


def _run_git(repo: str, args: list[str], timeout: int = 60) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            **utf8_text_kwargs(),
            timeout=timeout,
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "",
                "GIT_PAGER": "cat",
                "PAGER": "cat",
            },
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, out.strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _find_repo_root(start: str) -> str | None:
    """Walk upwards from *start* looking for a .git directory/worktree pointer."""
    cur = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(cur, ".git")) or os.path.isfile(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


class WorktreeManager:
    """Create / inspect / merge / discard task worktrees for one repository."""

    def __init__(self, repo_root: str):
        self.repo_root = os.path.normcase(os.path.abspath(repo_root))
        self.root_dir = os.path.join(self.repo_root, ".os-worktrees")
        self.refs_dir = os.path.join(self.root_dir, "_refs")
        self._ensure_excluded()

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------

    def _current_head_sha(self) -> str:
        ok, sha = _run_git(self.repo_root, ["rev-parse", "HEAD"])
        return sha.strip() if ok else ""

    def _ensure_excluded(self) -> None:
        try:
            os.makedirs(self.refs_dir, exist_ok=True)
            info_exclude = os.path.join(self.repo_root, ".git", "info", "exclude")
            # Detached worktrees keep .git as a FILE — resolve via git itself.
            if not os.path.isfile(info_exclude):
                ok, gitdir_out = _run_git(self.repo_root, ["rev-parse", "--git-dir"])
                if ok and gitdir_out:
                    info_exclude = os.path.normpath(os.path.join(self.repo_root, gitdir_out, "info", "exclude"))
            if os.path.isfile(info_exclude):
                with open(info_exclude, encoding="utf-8") as fh:
                    existing = fh.read()
                marker = "/.os-worktrees/"
                if marker not in existing:
                    with open(info_exclude, "a", encoding="utf-8") as fh:
                        if not existing.endswith("\n"):
                            fh.write("\n")
                        fh.write(marker + "\n")
        except Exception:
            logger.debug("[worktree] ensure exclude failed", exc_info=True)

    # ------------------------------------------------------------------
    # config
    # ------------------------------------------------------------------

    @staticmethod
    def enabled() -> bool:
        try:
            from opensquad._syscfg import raw as syscfg_raw

            cfg = syscfg_raw().get("worktree")
            if isinstance(cfg, dict) and "enabled" in cfg:
                return bool(cfg["enabled"])
        except Exception:
            pass
        return True

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def create(self, task_id: str, new_branch: bool = True) -> dict:
        """Create a worktree for *task_id*; returns metadata or raises."""
        tid = _sanitize_task_id(task_id)
        wt_path = os.path.join(self.root_dir, tid)
        if os.path.exists(wt_path):
            raise FileExistsError(f"worktree already exists: {wt_path}")

        ok, base_ref = _run_git(self.repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
        if not ok or not base_ref:
            raise RuntimeError(f"cannot resolve base branch: {base_ref}")
        base_ref = base_ref.strip()

        branch = f"os-task/{tid[:48]}"
        ref_path = os.path.join(self.refs_dir, f"{tid}.json")
        meta: dict = {
            "task_id": tid,
            "branch": branch if new_branch else None,
            "base_ref": base_ref,
            "base_sha": self._current_head_sha(),
            "worktree_path": wt_path,
            "created_at": time.time(),
        }

        args = ["worktree", "add", wt_path]
        if new_branch:
            args += ["-b", branch]
        args.append(base_ref)
        ok, out = _run_git(self.repo_root, args, timeout=120)
        if not ok:
            raise RuntimeError(f"git worktree add failed: {out}")

        with open(ref_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        logger.info("[worktree] created task=%s path=%s base=%s", tid, wt_path, base_ref)
        return meta

    def get(self, task_id: str) -> dict | None:
        ref_path = os.path.join(self.refs_dir, f"{_sanitize_task_id(task_id)}.json")
        if not os.path.isfile(ref_path):
            return None
        try:
            with open(ref_path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    def list(self) -> list[dict]:
        out: list[dict] = []
        if not os.path.isdir(self.refs_dir):
            return out
        for name in sorted(os.listdir(self.refs_dir)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.refs_dir, name), encoding="utf-8") as fh:
                    out.append(json.load(fh))
            except Exception:
                continue
        return out

    # ------------------------------------------------------------------
    # change reporting (merge decision input)
    # ------------------------------------------------------------------

    def _worktree_diff_stat(self, worktree_path: str, base_sha: str) -> tuple[bool, str, str]:
        """Uncommitted changes inside the worktree's working copy, diffed
        against the pinned *base_sha* (not the branch tip — task commits made
        by ``merge()`` must not be double-counted)."""
        ok, head = _run_git(worktree_path, ["rev-parse", "HEAD"], timeout=30)
        if not ok or not head:
            return False, "", ""
        ok, stat = _run_git(
            worktree_path,
            ["diff", "--stat", f"{base_sha}..{head}"],
            timeout=60,
        )
        ok2, stat2 = _run_git(worktree_path, ["diff", "--stat"], timeout=60)
        ok3, name_status = _run_git(
            worktree_path,
            ["diff", "--name-status", f"{base_sha}..{head}"],
            timeout=60,
        )
        ok4, name_status2 = _run_git(worktree_path, ["diff", "--name-status"], timeout=60)
        # `git diff` does not report untracked files — surface them via
        # `git status --porcelain` so the report reflects everything the
        # agent created (new files dominate typical task outputs).
        ok5, porcelain = _run_git(worktree_path, ["status", "--porcelain"], timeout=60)
        extra_stat_lines: list[str] = []
        extra_ns_lines: list[str] = []
        if ok5 and porcelain:
            for line in porcelain.splitlines():
                if not line.strip():
                    continue
                code = line[:2]
                path = line[3:].strip().strip('"')
                if not path or " -> " in code:
                    continue
                # Already captured by the diff-based scans?
                if any(path == p for p in name_status.split()) or any(path == p for p in name_status2.split()):
                    continue
                if code.strip() in ("??", "A") or code.strip().endswith("?"):
                    try:
                        n = sum(
                            1
                            for _ in open(
                                os.path.join(worktree_path, path),
                                encoding="utf-8",
                                errors="replace",
                            )
                        )
                    except OSError:
                        n = 0
                    extra_stat_lines.append(f" {path} | {n} +")
                    extra_ns_lines.append(f"A\t{path}")
        merged_stat = "\n".join(x for x in [stat if ok else "", stat2 if ok2 else "", *extra_stat_lines] if x)
        merged_ns = "\n".join(
            x for x in [name_status if ok3 else "", name_status2 if ok4 else "", *extra_ns_lines] if x
        )
        return True, merged_stat, merged_ns

    def diff_report(self, task_id: str) -> dict:
        """Human-readable + machine-consumable change summary for the task.

        Combines (a) committed task-branch changes (``base_sha...branch``) and
        (b) uncommitted edits in the worktree working copy — the common case
        where the agent edited files but never committed."""
        meta = self.get(task_id)
        if not meta:
            return {"status": "error", "message": f"unknown task {task_id}"}
        branch = meta.get("branch") or "HEAD"
        base = meta.get("base_sha") or meta["base_ref"]

        stat_parts: list[str] = []
        files: list[dict] = []
        commits: list[str] = []
        extra_insertions = 0  # untracked files don't appear in git's summary line

        # (a) committed changes on the task branch
        ok, stat = _run_git(
            self.repo_root,
            ["diff", "--stat", f"{base}...{branch}"],
            timeout=60,
        )
        ok2, name_status = _run_git(
            self.repo_root,
            ["diff", "--name-status", f"{base}...{branch}"],
            timeout=60,
        )
        ok3, log_out = _run_git(
            self.repo_root,
            ["log", "--oneline", f"{base}..{branch}"],
            timeout=60,
        )
        if ok and stat:
            stat_parts.append(stat)
        if ok2:
            for line in name_status.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    files.append({"status": parts[0], "path": parts[-1]})
        if ok3 and log_out:
            commits.extend(log_out.splitlines())

        # (b) uncommitted changes inside the worktree
        wt = meta.get("worktree_path", "")
        if wt and os.path.isdir(wt):
            ok_w, stat_w, ns_w = self._worktree_diff_stat(wt, base)
            if ok_w:
                if stat_w:
                    stat_parts.append(stat_w)
                for line in ns_w.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        entry = {"status": parts[0], "path": parts[-1]}
                        if entry not in files:
                            files.append(entry)
                            if entry["status"].startswith("A") or entry["status"] == "??":
                                try:
                                    extra_insertions += sum(
                                        1
                                        for _ in open(
                                            os.path.join(wt, entry["path"]),
                                            encoding="utf-8",
                                            errors="replace",
                                        )
                                    )
                                except OSError:
                                    pass

        merged_stat = "\n".join(stat_parts)
        return {
            "status": "ok",
            "task_id": meta["task_id"],
            "base_ref": meta["base_ref"],
            "base_sha": meta.get("base_sha", ""),
            "branch": branch,
            "worktree_path": wt,
            "stat": merged_stat,
            "files": files,
            "commits": commits,
            "insertions": _stat_insertions(merged_stat) + extra_insertions,
            "deletions": _stat_deletions(merged_stat),
        }

    # ------------------------------------------------------------------
    # merge / discard
    # ------------------------------------------------------------------

    def merge(self, task_id: str, strategy: str = "squash") -> dict:
        """Merge the task branch back into base. Never auto-resolves conflicts —
        on conflict the base tree is restored and an error is returned so the
        user can resolve manually inside the worktree."""
        meta = self.get(task_id)
        if not meta:
            return {"status": "error", "message": f"unknown task {task_id}"}
        branch = meta.get("branch")
        if not branch:
            return {"status": "error", "message": "task has no dedicated branch"}
        strategy = strategy if strategy in ("squash", "merge", "rebase") else "squash"

        # The task's edits live in the worktree's working copy (agent tools
        # rarely commit). Commit them onto the task branch first so `git merge`
        # can pick them up; empty commit is fine (idempotent).
        wt_path = meta["worktree_path"]
        if os.path.isdir(wt_path):
            safe_tid = re.sub(r"[^A-Za-z0-9]+", "-", meta["task_id"]).strip("-") or "task"
            _run_git(wt_path, ["add", "-A"], timeout=60)
            _run_git(
                wt_path,
                ["commit", "-m", f"os: task {safe_tid} changes", "--allow-empty"],
                timeout=60,
            )

        ok, out = _run_git(self.repo_root, ["merge", "--squash", branch], timeout=180)
        if not ok:
            _run_git(self.repo_root, ["merge", "--abort"])
            return {
                "status": "error",
                "message": "merge conflict (base branch moved); worktree kept for manual resolution",
                "detail": out,
            }
        ok, commit_out = _run_git(
            self.repo_root,
            ["commit", "-m", f"os: merge task {meta['task_id']}"],
            timeout=60,
        )
        if not ok and "nothing to commit" not in commit_out.lower():
            return {"status": "error", "message": f"commit failed: {commit_out}"}
        self._cleanup_git_artifacts(task_id, delete_branch=True)
        # _cleanup drops the branch but leaves the worktree directory itself —
        # remove it so the merge fully releases the task's disk footprint.
        wt_left = meta.get("worktree_path", "")
        if wt_left and os.path.isdir(wt_left):
            ok_rm, out_rm = _run_git(self.repo_root, ["worktree", "remove", "--force", wt_left], timeout=120)
            if not ok_rm:
                logger.warning("[worktree] post-merge worktree remove failed: %s", out_rm)
        return {"status": "ok", "message": commit_out or "merged (empty)"}

    def discard(self, task_id: str) -> dict:
        """Remove the worktree + task branch (keeps user work inside the main tree untouched)."""
        meta = self.get(task_id)
        if not meta:
            return {"status": "error", "message": f"unknown task {task_id}"}
        wt_path = meta["worktree_path"]
        if os.path.isdir(wt_path):
            ok, out = _run_git(self.repo_root, ["worktree", "remove", "--force", wt_path], timeout=120)
            if not ok:
                # Windows file locks occasionally linger — retry once after a short wait.
                time.sleep(1.0)
                ok, out = _run_git(self.repo_root, ["worktree", "remove", "--force", wt_path], timeout=120)
                if not ok:
                    return {"status": "error", "message": f"worktree remove failed: {out}"}
        self._cleanup_git_artifacts(task_id, delete_branch=True)
        return {"status": "ok"}

    def _cleanup_git_artifacts(self, task_id: str, delete_branch: bool) -> None:
        meta = self.get(task_id)
        if meta and delete_branch and meta.get("branch"):
            _run_git(self.repo_root, ["branch", "-D", meta["branch"]], timeout=60)
        try:
            os.remove(os.path.join(self.refs_dir, f"{task_id}.json"))
        except OSError:
            pass


def _summary_line(stat: str) -> str:
    """Last ``N files changed, X insertions(+), Y deletions(-)`` summary line
    of a ``git diff --stat`` output (empty string when none)."""
    for line in reversed([ln for ln in (stat or "").splitlines() if ln.strip()]):
        if "files changed" in line or "file changed" in line:
            return line.strip()
    return ""


def _stat_insertions(stat: str) -> int:
    line = _summary_line(stat)
    m = re.search(r"(\d+)\s+insertion", line)
    return int(m.group(1)) if m else 0


def _stat_deletions(stat: str) -> int:
    line = _summary_line(stat)
    m = re.search(r"(\d+)\s+deletion", line)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# Module-level convenience (used by TaskScheduler / Gateway API)
# ---------------------------------------------------------------------------

_managers: dict[str, WorktreeManager] = {}


def manager_for(path: str) -> WorktreeManager | None:
    """Return a WorktreeManager for the git repo containing *path*, or None."""
    key = os.path.normcase(os.path.abspath(path))
    if key in _managers:
        return _managers[key]
    repo = _find_repo_root(key)
    if not repo:
        return None
    mgr = WorktreeManager(repo)
    _managers[repo] = mgr
    return mgr


def prepare_task_workspace(project_dir: str, task_id: str) -> dict:
    """Entry point used when a parallel task session is spawned.

    Returns ``{"path": <working dir for the task>, "worktree": meta|None}``.
    Non-git workspaces fall back to a directory copy under
    ``<data>/os-tasks/<task_id>/`` when ``worktree.copy_fallback`` is enabled
    (default off — parallel writes into the same non-git tree are inherently
    unsafe, so the caller must explicitly opt in).
    """
    task_id = _sanitize_task_id(task_id)
    mgr = manager_for(project_dir)
    if mgr is not None and WorktreeManager.enabled():
        try:
            meta = mgr.create(task_id)
            return {"path": meta["worktree_path"], "worktree": meta}
        except FileExistsError:
            meta = mgr.get(task_id) or {}
            return {"path": meta.get("worktree_path", project_dir), "worktree": meta}
        except Exception:
            logger.warning("[worktree] create failed, using project dir", exc_info=True)
            return {"path": project_dir, "worktree": None}
    # copy fallback
    try:
        from opensquad._syscfg import raw as syscfg_raw

        cfg = syscfg_raw().get("worktree")
        if isinstance(cfg, dict) and cfg.get("copy_fallback"):
            target = os.path.join(syscfg.workspace_data_dir("os-tasks"), task_id)
            if not os.path.isdir(target):
                shutil.copytree(
                    project_dir, target, ignore=shutil.ignore_patterns(".git", "node_modules", ".os-worktrees")
                )
            return {"path": target, "worktree": None}
    except Exception:
        logger.warning("[worktree] copy fallback failed", exc_info=True)
    return {"path": project_dir, "worktree": None}


def new_task_id() -> str:
    return f"t{uuid.uuid4().hex[:12]}"
