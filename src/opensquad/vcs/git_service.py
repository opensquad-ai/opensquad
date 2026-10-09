"""Git plumbing for the Agent Web UI — the *user's* click path.

Deliberately separate from ``src/plugins/git_core/git_tools.py``. That plugin is
the agent's hands: it runs inside the agent process, and reaching it costs a full
LLM turn. This module is what the launcher calls when somebody clicks a branch,
stages a file or presses Push, so nothing here may depend on a model.

Both sides share the same rules, because they touch the same repository:

* **Hardening** — no terminal prompt, no pager, no locale-dependent output. The
  agent may be mid-command while the UI writes, so a prompt would hang the
  launcher forever and a localized message would break error classification.
* **One writer at a time** — the repository is a single resource, locked through
  :func:`opensquad.distributed_lock.SessionLock`. ``index.lock`` is the
  cross-process truth underneath; see :func:`repo_lock`.
* **Refs are never trusted** — branch names reach git as a single argv token, so
  a ref must pass :func:`validate_ref` and every pathspec is passed after ``--``.
  ``git switch`` / ``git restore`` are used instead of ``git checkout`` precisely
  because they cannot silently reinterpret a ref as a path.

Read-only side of the surface (status / branches / diff); the write ops live in
the same module below the "write operations" marker.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import time
from typing import Any

from opensquad.proc_text import utf8_text_kwargs

logger = logging.getLogger(__name__)

__all__ = [
    "abort_merge",
    "branches",
    "checkout",
    "commit",
    "delete_branch",
    "diff_file",
    "discard",
    "fetch",
    "find_repo_root",
    "git_available",
    "init_repo",
    "is_git_repo",
    "is_lock_contention",
    "pull",
    "push",
    "repo_key",
    "repo_lock",
    "safe_rel",
    "stage",
    "status",
    "undo_last_commit",
    "unstage",
    "validate_ref",
]

# Local commands are quick; network ones are started as background tasks instead
# of being run inside an HTTP request (the gateway proxy gives up at 30-60s).
_GIT_TIMEOUT = 20
_NET_TIMEOUT = 120

#: ASCII characters a branch name may never contain: git's own forbidden set
#: (space ``~^:?*[\``), every control character, and the shell metacharacters we
#: refuse on principle. Non-ASCII is deliberately *allowed* — 中文分支名 is a
#: normal thing to create here, and git itself permits it.
_FORBIDDEN_ASCII = frozenset(" ~^:?*[\\") | frozenset(";`$&|<>(){}!'\"") | {chr(c) for c in range(0x20)} | {"\x7f"}

#: Set on the process, not the repo: nothing here may rewrite user config.
_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "LANG": "C",
    "LC_ALL": "C",
}


# ---------------------------------------------------------------------------
# process plumbing
# ---------------------------------------------------------------------------


def _git(
    repo: str,
    args: list[str],
    *,
    timeout: int = _GIT_TIMEOUT,
    check: bool = False,
) -> tuple[int, str, str]:
    """Run ``git`` with *repo* as the working directory.

    ``cwd=`` rather than ``git -C``: the path arrives from Python (native Windows
    form under the launcher) and passing it as the child's cwd lets the OS do the
    translation, instead of asking git to re-parse a path it may not recognise.

    ``core.quotepath=false`` keeps CJK paths readable as themselves rather than
    octal escapes in the commands that print paths in a human format (the ones
    using ``-z`` are unaffected: NUL separation is already exact).
    """
    cmd = ["git", "-c", "core.quotepath=false", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=repo or None,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, **_GIT_ENV},
            **utf8_text_kwargs(),
        )
    except FileNotFoundError:
        return 127, "", "git executable not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"git timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a message
        return 1, "", str(exc)
    out = proc.stdout or ""
    err = proc.stderr or ""
    if check and proc.returncode != 0:
        logger.debug("[vcs] git %s failed (%s): %s", args[:1], proc.returncode, err.strip())
    return proc.returncode, out, err


def git_available() -> bool:
    """Whether a usable ``git`` is on PATH (the UI degrades to a hint if not)."""
    try:
        proc = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            timeout=10,
            env={**os.environ, **_GIT_ENV},
            **utf8_text_kwargs(),
        )
    except Exception:  # noqa: BLE001
        return False
    return proc.returncode == 0


def find_repo_root(start: str) -> str | None:
    """Walk upwards from *start* for a ``.git`` dir (or worktree pointer file).

    Mirrors ``workspace/worktree_manager._find_repo_root``: a session may be
    bound to a *worktree* (``.os-worktrees/<task>``, where ``.git`` is a file) or
    to a subdirectory of a repository, and both must report the real root.
    """
    if not start:
        return None
    cur = os.path.abspath(start)
    if not os.path.isdir(cur):
        cur = os.path.dirname(cur)
    while True:
        if os.path.isdir(os.path.join(cur, ".git")) or os.path.isfile(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def is_git_repo(path: str) -> bool:
    return bool(path) and find_repo_root(path) is not None


def repo_key(repo_root: str) -> str:
    """Stable, filesystem-safe lock id for one repository."""
    norm = os.path.normcase(os.path.abspath(repo_root))
    return "git_repo_" + hashlib.sha1(norm.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:16]


def repo_lock(repo_root: str, *, timeout: float = 8.0):
    """Cross-process exclusive lock for write operations on *repo_root*.

    ``git`` itself serialises writers through ``.git/index.lock``, but the loser
    of that race gets an exception mid-operation rather than a queue ticket. The
    agent's shell/tools and this UI are different processes, so the only useful
    mutex is an OS file lock: :class:`SessionLock` uses ``msvcrt`` on Windows and
    ``flock`` elsewhere. Callers pass a short timeout and turn
    :class:`~opensquad.distributed_lock.LockTimeoutError` into a "repository is
    busy" message instead of blocking a request thread.
    """
    from opensquad.distributed_lock import SessionLock

    return SessionLock(repo_key(repo_root), timeout=timeout)


def is_lock_contention(stderr: str) -> bool:
    """Whether a failed git call lost the ``index.lock`` race."""
    low = (stderr or "").lower()
    if "index.lock" in low or "another git process" in low:
        return True
    return "unable to create" in low and ".lock" in low


def validate_ref(name: str) -> str:
    """Return *name* if it is a safe branch/ref name, else raise ``ValueError``.

    The caller's string becomes one argv token, so this is deliberately stricter
    than "git will reject it later": a leading ``-`` would be read as an option,
    ``..`` / ``@{`` would reach unintended history, and a control character would
    corrupt the porcelain output we parse. Anything git accepts *and* that cannot
    be mistaken for an option, a path or a shell fragment is allowed through —
    including non-ASCII names.
    """
    raw = (name or "").strip()
    if not raw:
        raise ValueError("Branch name is empty")
    if len(raw) > 255:
        raise ValueError("Branch name is too long")
    if raw[0] in "-./" or raw[-1] in "/.":
        raise ValueError(f"Invalid branch name: {name!r}")
    if raw in ("@", "HEAD") or raw.endswith(".lock"):
        raise ValueError(f"Invalid branch name: {name!r}")
    if ".." in raw or "@{" in raw or "//" in raw or "\\" in raw:
        raise ValueError(f"Invalid branch name: {name!r}")
    if any(ch in _FORBIDDEN_ASCII for ch in raw):
        raise ValueError(f"Invalid branch name: {name!r}")
    return raw


def safe_rel(repo_root: str, rel: str) -> str | None:
    """Repo-relative path inside *repo_root*, or ``None`` when it escapes."""
    if not rel:
        return None
    rel = rel.replace("\\", "/").strip()
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        # Absolute input: accept only when it really is inside the repo.
        candidate = os.path.normcase(os.path.abspath(rel))
        root = os.path.normcase(os.path.abspath(repo_root))
        try:
            if os.path.commonpath([root, candidate]) != root:
                return None
        except ValueError:
            return None
        return os.path.relpath(candidate, repo_root).replace("\\", "/")
    if rel.startswith("./"):
        rel = rel[2:]
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts) or None


# ---------------------------------------------------------------------------
# porcelain v2
# ---------------------------------------------------------------------------


def _parse_status_v2(raw: str) -> dict[str, Any]:
    """Parse ``git status --porcelain=v2 --branch --show-stash -z`` output.

    Pure on purpose: the format is machine-readable and stable, so it is the one
    place worth pinning with unit tests instead of shelling out to git for every
    assertion.

    Record shapes (NUL-terminated with ``-z``; a rename adds one extra field)::

        # branch.oid <sha>|(initial)
        # branch.head <name>|(detached)
        # branch.upstream <name>          (upstream configured)
        # branch.ab +<ahead> -<behind>
        # stash <N>
        1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
        2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path>\\0<origPath>
        u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
        ? <path>
    """
    fields = raw.split("\0")
    info: dict[str, Any] = {
        "head_sha": "",
        "initial": False,
        "branch": None,
        "detached": False,
        "upstream": None,
        "ahead": 0,
        "behind": 0,
        "stash_count": 0,
    }
    staged: list[dict[str, Any]] = []
    unstaged: list[dict[str, Any]] = []
    untracked: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    i = 0
    while i < len(fields):
        rec = fields[i]
        i += 1
        if not rec:
            continue
        if rec.startswith("# "):
            body = rec[2:]
            if body.startswith("branch.oid "):
                oid = body[len("branch.oid ") :].strip()
                if oid == "(initial)":
                    info["initial"] = True
                else:
                    info["head_sha"] = oid
            elif body.startswith("branch.head "):
                head = body[len("branch.head ") :].strip()
                if head == "(detached)":
                    info["detached"] = True
                    info["branch"] = None
                else:
                    info["branch"] = head
            elif body.startswith("branch.upstream "):
                info["upstream"] = body[len("branch.upstream ") :].strip() or None
            elif body.startswith("branch.ab "):
                m = re.match(r"\+(\d+)\s+-(\d+)", body[len("branch.ab ") :].strip())
                if m:
                    info["ahead"] = int(m.group(1))
                    info["behind"] = int(m.group(2))
            elif body.startswith("stash "):
                try:
                    info["stash_count"] = int(body[len("stash ") :].strip() or 0)
                except ValueError:
                    info["stash_count"] = 0
            continue

        kind = rec[0]
        if kind in ("1", "2"):
            # ``1``/``2`` differ by one field (the rename score), so the path
            # starts at a different index: splitting with the wrong maxsplit
            # glues "R100" onto the filename.
            if kind == "1":
                parts = rec.split(" ", 8)
                if len(parts) < 9:
                    continue
                xy, path, orig = parts[1], parts[8], None
            else:
                parts = rec.split(" ", 9)
                if len(parts) < 10:
                    continue
                xy, path = parts[1], parts[9]
                # With -z the original path is the next NUL-separated field.
                orig = fields[i] if i < len(fields) else None
                i += 1
            entry = {"path": path, "orig_path": orig}
            x, y = xy[0], xy[1] if len(xy) > 1 else "."
            if x != ".":
                staged.append({**entry, "status": x})
            if y != ".":
                unstaged.append({**entry, "status": y})
        elif kind == "u":
            parts = rec.split(" ", 10)
            if len(parts) < 11:
                continue
            xy = parts[1]
            conflicts.append({"path": parts[10], "status": xy, "orig_path": None})
        elif kind == "?":
            untracked.append({"path": rec[2:], "status": "?", "orig_path": None})

    info["staged"] = staged
    info["unstaged"] = unstaged
    info["untracked"] = untracked
    info["conflicts"] = conflicts
    info["counts"] = {
        "staged": len(staged),
        "unstaged": len(unstaged),
        "untracked": len(untracked),
        "conflicts": len(conflicts),
        # The number behind "n uncommitted files" in the status bar: every path
        # that differs from HEAD, counted once.
        "total": len(
            {e["path"] for e in staged}
            | {e["path"] for e in unstaged}
            | {e["path"] for e in untracked}
            | {e["path"] for e in conflicts}
        ),
    }
    return info


def _in_progress(repo_root: str) -> dict[str, bool]:
    """Which multi-step operation is half-done (drives the UI's conflict banner)."""
    return {
        "merge": os.path.isfile(os.path.join(repo_root, ".git", "MERGE_HEAD")),
        "rebase": os.path.isdir(os.path.join(repo_root, ".git", "rebase-merge"))
        or os.path.isdir(os.path.join(repo_root, ".git", "rebase-apply")),
        "cherry_pick": os.path.isfile(os.path.join(repo_root, ".git", "CHERRY_PICK_HEAD")),
        "revert": os.path.isfile(os.path.join(repo_root, ".git", "REVERT_HEAD")),
    }


def status(cwd: str, *, include_in_progress: bool = True) -> dict[str, Any]:
    """Repository status for the status bar. Degrades instead of raising.

    A non-repo answers ``{"is_repo": False}`` — the UI shows "非 Git 仓库" with an
    init action, and the panel keeps whatever the snapshot store reports.
    """
    cwd_abs = os.path.abspath(cwd) if cwd else ""
    if not cwd_abs or not os.path.isdir(cwd_abs):
        return {"is_repo": False, "cwd": cwd_abs, "error": "Working directory not found"}
    repo_root = find_repo_root(cwd_abs)
    if not repo_root:
        return {"is_repo": False, "cwd": cwd_abs, "repo_root": None}
    if not git_available():
        return {"is_repo": False, "cwd": cwd_abs, "repo_root": repo_root, "error": "git not found on PATH"}

    code, out, err = _git(
        repo_root,
        ["status", "--porcelain=v2", "--branch", "--show-stash", "--untracked-files=all", "-z"],
    )
    if code != 0:
        return {"is_repo": False, "cwd": cwd_abs, "repo_root": repo_root, "error": err.strip() or "git status failed"}

    data = _parse_status_v2(out)
    remotes: list[dict[str, str]] = []
    rc, rem_out, _ = _git(repo_root, ["remote", "-v"])
    if rc == 0:
        seen: set[str] = set()
        for line in rem_out.splitlines():
            bits = line.split()
            if len(bits) >= 2 and bits[0] not in seen:
                seen.add(bits[0])
                remotes.append({"name": bits[0], "url": bits[1]})

    data.update(
        {
            "is_repo": True,
            "cwd": cwd_abs,
            "repo_root": repo_root,
            "name": os.path.basename(repo_root.rstrip("/\\")) or repo_root,
            "remotes": remotes,
            "git_version": _git(repo_root, ["--version"])[1].strip().replace("git version ", ""),
        }
    )
    if include_in_progress:
        data["in_progress"] = _in_progress(repo_root)
    return data


# ---------------------------------------------------------------------------
# branches
# ---------------------------------------------------------------------------

_BRANCH_FMT = "%00".join(
    [
        "%(refname)",
        "%(objectname:short)",
        "%(committerdate:unix)",
        "%(authorname)",
        "%(subject)",
        "%(upstream:short)",
        "%(upstream:track)",
        "%(HEAD)",
        "%(symref)",
    ]
)

_TRACK_RE = re.compile(r"ahead\s+(\d+)|behind\s+(\d+)")


def _parse_track(track: str) -> tuple[int, int]:
    """``[ahead 2, behind 1]`` → ``(2, 1)``; ``[gone]`` → ``(0, 0)``."""
    ahead = behind = 0
    for m in _TRACK_RE.finditer(track or ""):
        if m.group(1):
            ahead = int(m.group(1))
        elif m.group(2):
            behind = int(m.group(2))
    return ahead, behind


def branches(cwd: str) -> dict[str, Any]:
    """Local + remote branches with the data the picker shows.

    One ``for-each-ref`` call: ``%(committerdate:unix)`` feeds the UI's
    localized relative time (never ``:relative``, which is locale-dependent and
    would arrive English from here), and ``%(upstream:track)`` supplies
    ahead/behind per branch so the picker can flag "3 to push" without a second
    call per row.
    """
    repo_root = find_repo_root(os.path.abspath(cwd) if cwd else "")
    if not repo_root:
        raise ValueError("Not a git repository")
    code, out, err = _git(repo_root, ["for-each-ref", f"--format={_BRANCH_FMT}", "refs/heads", "refs/remotes"])
    if code != 0:
        raise RuntimeError(err.strip() or "git for-each-ref failed")

    local: list[dict[str, Any]] = []
    remote: list[dict[str, Any]] = []
    default_branch: str | None = None
    current: str | None = None
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = (line.split("\0") + [""] * 9)[:9]
        refname, sha, ts, author, subject, upstream, track, head, symref = parts
        if symref:
            # refs/remotes/origin/HEAD is a symref to the default branch: use it
            # as a hint, never as a row.
            if refname.startswith("refs/remotes/") and refname.endswith("/HEAD"):
                default_branch = symref.rsplit("/", 1)[-1] or None
            continue
        if refname.endswith("/HEAD"):
            continue
        try:
            ts_i = int(ts or 0)
        except ValueError:
            ts_i = 0
        ahead, behind = _parse_track(track)
        if refname.startswith("refs/heads/"):
            name = refname[len("refs/heads/") :]
            is_current = head == "*"
            if is_current:
                current = name
            local.append(
                {
                    "name": name,
                    "sha": sha,
                    "ts": ts_i,
                    "author": author,
                    "subject": subject,
                    "upstream": upstream or None,
                    "ahead": ahead,
                    "behind": behind,
                    "gone": "[gone]" in (track or ""),
                    "current": is_current,
                }
            )
        elif refname.startswith("refs/remotes/"):
            tail = refname[len("refs/remotes/") :]
            remote_name, _, short = tail.partition("/")
            remote.append(
                {
                    "name": short,
                    "remote": remote_name,
                    "sha": sha,
                    "ts": ts_i,
                    "author": author,
                    "subject": subject,
                }
            )

    local.sort(key=lambda b: (not b["current"], -b["ts"], b["name"]))
    remote.sort(key=lambda b: (-b["ts"], b["remote"], b["name"]))
    return {
        "repo_root": repo_root,
        "current": current,
        "default_branch": default_branch,
        "local": local,
        "remote": remote,
    }


# ---------------------------------------------------------------------------
# file diffs — rendered through the same builder as the session-changes pane
# ---------------------------------------------------------------------------


def _blob_text(repo_root: str, spec: str) -> tuple[str | None, bool]:
    """Return ``(text, binary)`` for a ``<rev>:<path>`` spec (``None`` = absent)."""
    code, out, _err = _git(repo_root, ["show", spec], timeout=_GIT_TIMEOUT)
    if code != 0:
        return None, False
    if "\0" in out:
        return None, True
    return out, False


def _disk_text(repo_root: str, rel: str) -> tuple[str | None, bool]:
    abs_path = os.path.join(repo_root, rel.replace("/", os.sep))
    if not os.path.isfile(abs_path):
        return None, False
    try:
        if os.path.getsize(abs_path) > 4 * 1024 * 1024:
            return None, True
        with open(abs_path, encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except Exception:  # noqa: BLE001
        return None, True
    if "\0" in text:
        return None, True
    return text, False


def diff_file(cwd: str, rel: str, *, mode: str = "worktree", collapse: bool = True) -> dict[str, Any]:
    """One file's git diff, shaped exactly like ``fs/session-diff``.

    ``mode='worktree'`` compares HEAD with the file on disk (everything not yet
    committed); ``mode='staged'`` compares HEAD with the index (what a commit
    would record). Both render through
    :func:`opensquad.utils.session_changeset.build_diff_lines`, so the git pane
    and the session pane produce identical rows and the UI keeps one viewer.
    """
    from opensquad.utils.session_changeset import build_diff_lines

    repo_root = find_repo_root(os.path.abspath(cwd) if cwd else "")
    if not repo_root:
        return {"error": "Not a git repository", "status": 400, "path": rel}
    safe = safe_rel(repo_root, rel)
    if not safe:
        return {"error": "Path outside the repository", "status": 400, "path": rel}

    # A fresh repo has no HEAD yet: every path is then an addition.
    head_code, head_out, _ = _git(repo_root, ["rev-parse", "--verify", "HEAD"])
    has_head = head_code == 0 and bool(head_out.strip())

    if mode == "staged":
        old, old_binary = _blob_text(repo_root, f"HEAD:{safe}") if has_head else (None, False)
        new, new_binary = _blob_text(repo_root, f":{safe}")
    else:
        old, old_binary = _blob_text(repo_root, f"HEAD:{safe}") if has_head else (None, False)
        new, new_binary = _disk_text(repo_root, safe)

    if old_binary or new_binary:
        return {
            "path": safe,
            "status": _letter(old, new),
            "additions": 0,
            "deletions": 0,
            "oversized": True,
            "lines": [],
        }

    lines = build_diff_lines(old, new, collapse=collapse)
    additions = sum(1 for ln in lines if ln["type"] == "insert")
    deletions = sum(1 for ln in lines if ln["type"] == "delete")
    return {
        "path": safe,
        "status": _letter(old, new),
        "additions": additions,
        "deletions": deletions,
        "lines": lines,
    }


def _letter(old: str | None, new: str | None) -> str:
    if old is None and new is None:
        return "?"
    if old is None:
        return "A"
    if new is None:
        return "D"
    return "M"


# ---------------------------------------------------------------------------
# write operations
# ---------------------------------------------------------------------------
#
# Every one of these returns ``{"ok": True, ...}`` or
# ``{"ok": False, "code": ..., "error": ...}``. The code is what the UI branches
# on (``dirty_worktree`` → ask about stashing, ``already_pushed`` → refuse the
# undo, ``locked`` → retry), so it is part of the contract, not decoration.


def _fail(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "code": code, "error": message, **extra}


def _ok(**extra: Any) -> dict[str, Any]:
    return {"ok": True, **extra}


def _repo_or_fail(cwd: str) -> tuple[str | None, dict[str, Any] | None]:
    root = find_repo_root(os.path.abspath(cwd) if cwd else "")
    if not root:
        return None, _fail("not_a_repo", "Not a git repository")
    return root, None


def _rels_or_fail(root: str, paths: list[str] | None) -> tuple[list[str] | None, dict[str, Any] | None]:
    """Validate the whole pathspec batch, or refuse it as a unit."""
    rels: list[str] = []
    for raw in paths or []:
        rel = safe_rel(root, str(raw or ""))
        if not rel:
            return None, _fail("bad_path", f"Path outside the repository: {raw!r}")
        rels.append(rel)
    if not rels:
        return None, _fail("no_paths", "No files given")
    return rels, None


def _git_write(root: str, args: list[str], *, timeout: int = _GIT_TIMEOUT, attempts: int = 2) -> tuple[int, str, str]:
    """A mutating git call, retried once when git's own ``index.lock`` is held.

    :func:`repo_lock` only excludes *our* writers; the agent's shell can still be
    inside a git command, and git answers that race with ``index.lock`` rather
    than by queueing. One short retry turns the common collision into a success
    instead of an error the user can do nothing about.
    """
    code, out, err = _git(root, args, timeout=timeout)
    if code != 0 and attempts > 1 and is_lock_contention(err):
        time.sleep(0.3)
        code, out, err = _git(root, args, timeout=timeout)
    return code, out, err


def _run_locked(root: str, fn: Any, *, timeout: float = 8.0) -> dict[str, Any]:
    """Run *fn* with the repository locked; contention becomes a usable answer."""
    from opensquad.distributed_lock import LockTimeoutError

    try:
        with repo_lock(root, timeout=timeout):
            return fn()
    except LockTimeoutError:
        return _fail("locked", "Repository is busy — another git operation is in progress.")


def _has_head(root: str) -> bool:
    code, out, _ = _git(root, ["rev-parse", "--verify", "HEAD"])
    return code == 0 and bool(out.strip())


def _current_branch(root: str) -> str | None:
    code, out, _ = _git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    name = out.strip()
    return name if code == 0 and name and name != "HEAD" else None


def _dirty_state(root: str) -> dict[str, Any]:
    """Dirty paths + counts, parsed exactly like the status bar does."""
    code, out, _ = _git(root, ["status", "--porcelain=v2", "--untracked-files=all", "-z"])
    if code != 0:
        return {"counts": {"total": 0}, "paths": [], "untracked": set()}
    data = _parse_status_v2(out)
    paths = sorted({e["path"] for e in data["staged"] + data["unstaged"] + data["untracked"] + data["conflicts"]})
    return {
        "counts": data["counts"],
        "paths": paths,
        "untracked": {e["path"] for e in data["untracked"]},
    }


def init_repo(cwd: str, *, initial_branch: str = "main") -> dict[str, Any]:
    """``git init`` a workspace that is not a repository yet.

    The status bar offers this instead of hiding itself, because a folder nobody
    initialised would otherwise never show the feature exists. ``-b`` is given
    explicitly so the result does not depend on the user's ``init.defaultBranch``;
    git older than 2.28 ignores the flag and needs the symref fixed afterwards.
    """
    target = os.path.abspath(cwd) if cwd else ""
    if not target or not os.path.isdir(target):
        return _fail("not_a_directory", "Working directory not found")
    if is_git_repo(target):
        return _fail("already_repo", "Already a git repository")
    name = validate_ref(initial_branch)
    code, _out, err = _git(target, ["init", "-b", name])
    if code != 0:
        code, _out, err = _git(target, ["init"])
        if code == 0:
            _git(target, ["symbolic-ref", "HEAD", f"refs/heads/{name}"])
    if code != 0:
        return _fail("init_failed", err.strip() or "git init failed")
    root = find_repo_root(target) or target
    return _ok(repo_root=root, branch=name)


def checkout(
    cwd: str,
    branch: str,
    *,
    create: bool = False,
    base: str | None = None,
    stash_dirty: bool = False,
) -> dict[str, Any]:
    """Switch branch, optionally creating it, optionally stashing first.

    A dirty worktree is **not** stashed behind the user's back: without
    ``stash_dirty`` the call answers ``dirty_worktree`` with the file list, and
    the UI asks. ``git switch`` is used rather than ``git checkout`` so a typo
    cannot turn the ref into a pathspec.

    When a stash was taken and the switch then fails, the stash is popped back:
    the user must never end up with their work parked in a stash they did not
    ask for.
    """
    root, err = _repo_or_fail(cwd)
    if err:
        return err
    try:
        name = validate_ref(branch)
        base_ref = validate_ref(base) if base else None
    except ValueError as exc:
        return _fail("bad_ref", str(exc))

    def _switch() -> dict[str, Any]:
        state = _dirty_state(root)
        dirty = bool(state["counts"]["total"])
        if dirty and not stash_dirty:
            return _fail(
                "dirty_worktree",
                "The worktree has uncommitted changes",
                paths=state["paths"][:200],
                counts=state["counts"],
            )
        stashed = False
        if dirty:
            code, _out, err = _git_write(
                root, ["stash", "push", "--include-untracked", "-m", f"opensquad: switch to {name}"]
            )
            if code != 0:
                return _fail("stash_failed", err.strip() or "git stash failed")
            stashed = True

        args: list[str]
        if create and base_ref:
            args = ["switch", "-c", name, base_ref]
        elif create:
            args = ["switch", "-c", name]
        else:
            args = ["switch", name]
        code, _out, err = _git_write(root, args)
        if code != 0 and stashed:
            _git(root, ["stash", "pop"])
            return _fail(
                "checkout_failed",
                err.strip() or "git switch failed",
                unstashed=True,
            )
        if code != 0:
            return _fail("checkout_failed", err.strip() or "git switch failed")
        return _ok(branch=name, created=create, stashed=stashed)

    return _run_locked(root, _switch)


def delete_branch(cwd: str, branch: str, *, force: bool = False) -> dict[str, Any]:
    """Delete a branch; ``force`` is the UI's second confirmation for ``-D``.

    Deleting the checked-out branch is refused rather than passed to git: the
    message git produces for it is about the current worktree, which is not what
    the user meant to do.
    """
    root, err = _repo_or_fail(cwd)
    if err:
        return err
    try:
        name = validate_ref(branch)
    except ValueError as exc:
        return _fail("bad_ref", str(exc))
    if _current_branch(root) == name:
        return _fail("current_branch", "Cannot delete the checked-out branch")

    def _delete() -> dict[str, Any]:
        code, out, err = _git_write(root, ["branch", "-D" if force else "-d", name])
        if code != 0:
            message = (err or out).strip()
            # `branch -d` refusing an unmerged branch is a decision the user can
            # still make — surface it as such instead of a generic failure.
            if not force and "not fully merged" in message:
                return _fail("not_merged", message, branch=name)
            return _fail("delete_failed", message or "git branch -d failed")
        return _ok(branch=name)

    return _run_locked(root, _delete)


def stage(cwd: str, paths: list[str]) -> dict[str, Any]:
    """``git add --`` the given paths (index only; the worktree is untouched)."""
    root, err = _repo_or_fail(cwd)
    if err:
        return err
    rels, err = _rels_or_fail(root, paths)
    if err:
        return err
    return _run_locked(root, lambda: _stage_locked(root, rels))


def _stage_locked(root: str, rels: list[str]) -> dict[str, Any]:
    code, _out, err = _git_write(root, ["add", "--", *rels])
    if code != 0:
        return _fail("stage_failed", err.strip() or "git add failed")
    return _ok(staged=rels)


def unstage(cwd: str, paths: list[str]) -> dict[str, Any]:
    """Take the given paths back out of the index.

    ``git restore --staged`` needs a HEAD to restore *from*; in a repository
    whose first commit has not happened yet the index is dropped instead.
    """
    root, err = _repo_or_fail(cwd)
    if err:
        return err
    rels, err = _rels_or_fail(root, paths)
    if err:
        return err

    def _unstage() -> dict[str, Any]:
        has_head = _has_head(root)
        args = ["restore", "--staged", "--", *rels] if has_head else ["rm", "--cached", "-r", "--quiet", "--", *rels]
        code, _out, err = _git_write(root, args, attempts=2)
        if code != 0 and not has_head:
            # `git rm --cached` also fails for paths that were never added.
            return _ok(unstaged=[], note="nothing staged")
        if code != 0:
            return _fail("unstage_failed", err.strip() or "git restore --staged failed")
        return _ok(unstaged=rels)

    return _run_locked(root, _unstage)


def discard(cwd: str, paths: list[str], *, remove_untracked: bool = False) -> dict[str, Any]:
    """Throw away local changes for the given paths.

    Tracked paths are restored from HEAD (index and worktree). **Untracked**
    files have nothing to restore from, so discarding one means deleting it from
    disk — that only happens when the caller explicitly passes
    ``remove_untracked``, i.e. after the UI's own confirmation. Directories are
    never removed here; the file panel already owns delete.
    """
    root, err = _repo_or_fail(cwd)
    if err:
        return err
    rels, err = _rels_or_fail(root, paths)
    if err:
        return err

    def _discard() -> dict[str, Any]:
        if not _has_head(root):
            return _fail("no_head", "Nothing to restore from: the repository has no commits yet")
        state = _dirty_state(root)
        untracked = state["untracked"]
        keep = [rel for rel in rels if rel not in untracked]
        drop = [rel for rel in rels if rel in untracked]

        if keep:
            code, _out, err = _git_write(root, ["restore", "--source=HEAD", "--staged", "--worktree", "--", *keep])
            if code != 0:
                return _fail("discard_failed", err.strip() or "git restore failed")
        if drop and not remove_untracked:
            return _fail(
                "untracked_needs_confirmation",
                "Untracked files are deleted, not restored",
                paths=drop,
                restored=keep,
                dirty=state["paths"][:200],
            )
        removed: list[str] = []
        for rel in drop:
            target = os.path.join(root, rel.replace("/", os.sep))
            if os.path.isfile(target):
                try:
                    os.remove(target)
                    removed.append(rel)
                except OSError as exc:
                    return _fail("discard_failed", f"{rel}: {exc}")
        return _ok(restored=keep, removed=removed)

    return _run_locked(root, _discard)


def commit(cwd: str, title: str, description: str = "") -> dict[str, Any]:
    """Commit the index (the UI stages first, then commits — never both at once).

    Committing only what is staged keeps the checked boxes in the panel and the
    commit contents the same thing. A title is required, and an empty index is
    refused rather than producing an empty commit. Repository hooks run: a commit
    the user asked for is a real commit, so ``--no-verify`` stays out of this
    path.
    """
    root, err = _repo_or_fail(cwd)
    if err:
        return err
    subject = (title or "").strip()
    if not subject:
        return _fail("empty_message", "A commit title is required")
    body = (description or "").strip()
    message = f"{subject}\n\n{body}\n" if body else subject

    def _commit() -> dict[str, Any]:
        code, out, _ = _git(root, ["diff", "--cached", "--name-only"])
        if code != 0:
            return _fail("commit_failed", "Could not read the index")
        if not out.strip():
            return _fail("nothing_staged", "Stage the files to commit first")
        code, _out, err = _git_write(root, ["commit", "-m", message], timeout=60)
        if code != 0:
            return _fail("commit_failed", err.strip() or "git commit failed")
        _c, sha, _e = _git(root, ["rev-parse", "--short", "HEAD"])
        return _ok(sha=sha.strip(), subject=subject)

    return _run_locked(root, _commit)


def undo_last_commit(cwd: str) -> dict[str, Any]:
    """Undo the last commit, keeping its changes staged — only if not yet pushed.

    The guard is ``git branch -r --contains HEAD``: an empty answer means no
    remote-tracking branch holds this commit, so rewriting it cannot disturb
    anybody else. Anything published is refused with ``already_pushed``; undoing
    a published commit is a force-push problem, not a local one.
    """
    root, err = _repo_or_fail(cwd)
    if err:
        return err

    def _undo() -> dict[str, Any]:
        code, out, _ = _git(root, ["branch", "-r", "--contains", "HEAD"])
        if code == 0 and out.strip():
            return _fail("already_pushed", "The last commit is already on a remote branch")
        code, _out, _err = _git(root, ["rev-parse", "--verify", "HEAD~1"])
        if code != 0:
            return _fail("no_parent", "The last commit is the first commit in the repository")
        code, _out, err = _git_write(root, ["reset", "--soft", "HEAD~1"])
        if code != 0:
            return _fail("undo_failed", err.strip() or "git reset failed")
        _c, sha, _e = _git(root, ["rev-parse", "--short", "HEAD"])
        return _ok(sha=sha.strip())

    return _run_locked(root, _undo)


# ---------------------------------------------------------------------------
# sync: fetch / pull / push
# ---------------------------------------------------------------------------
#
# These are the only calls that touch the network, and the only ones that can
# block for longer than a gateway request lives. They stay synchronous and
# bounded here (``_NET_TIMEOUT``); the launcher runs them as a background task
# with a polled progress record, so the UI is never holding a request open.


def _remote_or_fail(root: str, remote: str | None, *, required: bool = True) -> tuple[str | None, dict | None]:
    """Resolve the remote name to use, defaulting to the branch's upstream."""
    code, out, _ = _git(root, ["remote"])
    remotes = [r.strip() for r in out.splitlines() if r.strip()]
    if remote:
        if remote not in remotes:
            return None, _fail("no_remote", f"No such remote: {remote}", remotes=remotes)
        return remote, None
    if not remotes:
        if required:
            return None, _fail("no_remote", "This repository has no remote configured")
        return None, None
    upstream = _upstream_of(root)
    if upstream and "/" in upstream:
        name = upstream.split("/", 1)[0]
        if name in remotes:
            return name, None
    return remotes[0], None


def _upstream_of(root: str) -> str | None:
    code, out, _ = _git(root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    return out.strip() if code == 0 and out.strip() else None


def credentials() -> dict[str, str]:
    """Username/token for HTTPS remotes, from the same file the git agent uses.

    Read (never written) from ``data/plugins/git_core/config.json`` — the plugin
    that gives the *agent* git tools already stores an HTTPS token there, and
    asking the user for the same secret twice would be worse than reusing it.
    Secrets are only ever injected into an in-memory URL; nothing here writes a
    credential into the repository's config.
    """
    import json

    out: dict[str, str] = {}
    try:
        from opensquad.system_config import syscfg

        path = os.path.join(syscfg.project_root(), "data", "plugins", "git_core", "config.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                saved = json.load(fh)
            if isinstance(saved, dict):
                out["username"] = str(saved.get("username") or "")
                out["access_token"] = str(saved.get("access_token") or "")
    except Exception:  # noqa: BLE001 - missing config simply means "no credential"
        logger.debug("[vcs] git_core credential config unavailable", exc_info=True)
    return out


def _mask(text: str, *secrets: str) -> str:
    """Strip secrets out of anything we are about to return to the UI.

    Git echoes the remote URL in its own errors (``unable to access
    'https://user:token@host/…'``), so a failed push would otherwise hand the
    token straight to the browser.
    """
    from urllib.parse import quote

    masked = text or ""
    for secret in secrets:
        if not secret:
            continue
        for form in {secret, quote(secret, safe="")}:
            masked = masked.replace(form, "***")
    return masked


def _authed_url(url: str, *, username: str, token: str) -> str:
    """Embed credentials into an http(s) URL, in memory only."""
    if not token or not url.startswith(("http://", "https://")):
        return url
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)
    user = username or "oauth2"
    netloc = f"{user}:{token}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _sync_target(root: str, remote: str) -> tuple[str, str]:
    """What to hand git as the push/fetch target, plus the token to mask.

    An HTTPS remote with a configured token is addressed by its credentialled
    URL for this one call, so the credential never lands in ``.git/config``.
    """
    code, out, _ = _git(root, ["remote", "get-url", remote])
    url = out.strip() if code == 0 else ""
    creds = credentials()
    token = creds.get("access_token", "")
    if url and token and url.startswith(("http://", "https://")):
        return _authed_url(url, username=creds.get("username", ""), token=token), token
    return remote, token


def classify_sync_error(text: str) -> tuple[str, str]:
    """Map git's wording to the code the UI branches on.

    Every branch here is something the UI must answer differently: a rejected
    push needs a "pull first" offer, a credential failure needs a settings link,
    and a missing upstream needs a "publish this branch" button.
    """
    low = (text or "").lower()
    if "non-fast-forward" in low or "fetch first" in low or ("[rejected]" in low and "behind" in low):
        return "non_fast_forward", "The remote has commits you do not have yet — pull, then push."
    if "stale info" in low or "--force-with-lease" in low:
        return "stale_lease", "The remote moved since your last fetch — fetch and try again."
    if (
        "authentication failed" in low
        or "could not read username" in low
        or "could not read password" in low
        or "permission denied" in low
        or "invalid username or password" in low
        or "returned error: 401" in low
        or "returned error: 403" in low
    ):
        return "auth_failed", "The remote rejected the credentials."
    if "no configured push destination" in low or "no upstream branch" in low or "has no upstream branch" in low:
        return "no_upstream", "This branch has no upstream yet — publish it first."
    if "repository" in low and "not found" in low:
        return "no_remote", "The remote repository could not be found."
    if "could not resolve host" in low or "connection timed out" in low or "network is unreachable" in low:
        return "network", "Could not reach the remote."
    return "sync_failed", ""


def _sync_failure(code: int, out: str, err: str, *, token: str) -> dict[str, Any]:
    text = _mask((err or out or "").strip(), token)
    kind, hint = classify_sync_error(text)
    return _fail(kind, text or "git failed", **({"hint": hint} if hint else {}))


def fetch(cwd: str, *, remote: str | None = None, prune: bool = True) -> dict[str, Any]:
    """Update remote-tracking refs. Never touches the worktree or the index."""
    root, err = _repo_or_fail(cwd)
    if err:
        return err
    name, err = _remote_or_fail(root, remote)
    if err:
        return err

    def _fetch() -> dict[str, Any]:
        target, token = _sync_target(root, name)
        args = ["fetch", "--prune" if prune else "--no-prune", target]
        code, out, err_text = _git(root, args, timeout=_NET_TIMEOUT)
        if code != 0:
            return _sync_failure(code, out, err_text, token=token)
        st = status(root, include_in_progress=False)
        return _ok(remote=name, ahead=st.get("ahead", 0), behind=st.get("behind", 0))

    return _run_locked(root, _fetch, timeout=_NET_TIMEOUT)


def pull(
    cwd: str,
    *,
    remote: str | None = None,
    branch: str | None = None,
    autostash: bool = True,
) -> dict[str, Any]:
    """Fetch and integrate.

    Divergence is merged rather than rebased: the history the user already
    pushed stays as it is, and a conflict is reported with git left mid-merge so
    the UI can show it file by file (``abort_merge`` is the way out).

    Local edits are merged over, never overwritten: ``git pull --autostash``
    parks them first. Without ``autostash`` a dirty worktree is refused with
    ``dirty_worktree`` so the caller can decide.
    """
    root, err = _repo_or_fail(cwd)
    if err:
        return err
    name, err = _remote_or_fail(root, remote)
    if err:
        return err
    try:
        ref = validate_ref(branch) if branch else None
    except ValueError as exc:
        return _fail("bad_ref", str(exc))

    def _pull() -> dict[str, Any]:
        state = _dirty_state(root)
        if state["counts"]["total"] and not autostash:
            return _fail(
                "dirty_worktree",
                "The worktree has uncommitted changes",
                paths=state["paths"][:200],
                counts=state["counts"],
            )
        target, token = _sync_target(root, name)
        args = ["pull", "--no-rebase"]
        if autostash:
            args.append("--autostash")
        args.append(target)
        if ref:
            args.append(ref)
        code, out, err_text = _git(root, args, timeout=_NET_TIMEOUT)
        st = status(root)
        conflicts = [e["path"] for e in st.get("conflicts", [])]
        if conflicts:
            # Conflict detection is *state*-first, not message-first: the same
            # fact the panel will render decides here, so a tree that is already
            # half-merged (or a future git wording) is still reported as
            # `conflicts` rather than as an opaque failure. `via` says where the
            # markers came from, because `merge --abort` is only the way out of
            # the first kind.
            return _fail(
                "conflicts",
                "The merge hit conflicts",
                conflicts=conflicts,
                via="merge" if st.get("in_progress", {}).get("merge") else "autostash",
                output=_mask((out or err_text or "").strip(), token),
            )
        if code != 0:
            return _sync_failure(code, out, err_text, token=token)
        merged = "merge made by" in out.lower()
        if "already up to date" in out.lower():
            mode = "up_to_date"
        else:
            mode = "merge" if merged else "fast_forward"
        return _ok(
            remote=name,
            mode=mode,
            ahead=st.get("ahead", 0),
            behind=st.get("behind", 0),
        )

    return _run_locked(root, _pull, timeout=_NET_TIMEOUT)


def push(
    cwd: str,
    *,
    remote: str | None = None,
    branch: str | None = None,
    set_upstream: bool = False,
    force_with_lease: bool = False,
) -> dict[str, Any]:
    """Push the current branch.

    Plain ``--force`` is not a parameter this surface has: the only rewrite on
    offer is ``--force-with-lease``, which refuses to clobber a remote that moved
    since the last fetch. A remote that rejects a normal push answers
    ``non_fast_forward`` with a ``pull_then_push`` hint, so the UI offers pulling
    instead of pushing harder.
    """
    root, err = _repo_or_fail(cwd)
    if err:
        return err
    name, err = _remote_or_fail(root, remote)
    if err:
        return err
    try:
        ref = validate_ref(branch) if branch else None
    except ValueError as exc:
        return _fail("bad_ref", str(exc))

    def _push() -> dict[str, Any]:
        current = ref or _current_branch(root)
        if not current:
            return _fail("detached_head", "Not on a branch — nothing to push")
        if not set_upstream and not _upstream_of(root):
            return _fail(
                "no_upstream",
                "This branch has no upstream yet — publish it first.",
                hint="set_upstream",
                branch=current,
            )
        target, token = _sync_target(root, name)
        args = ["push"]
        if force_with_lease:
            args.append("--force-with-lease")
        if set_upstream:
            args.append("--set-upstream")
        args += [target, current]
        code, out, err_text = _git(root, args, timeout=_NET_TIMEOUT)
        if code != 0:
            return _sync_failure(code, out, err_text, token=token)
        st = status(root, include_in_progress=False)
        return _ok(remote=name, branch=current, ahead=st.get("ahead", 0), behind=st.get("behind", 0))

    return _run_locked(root, _push, timeout=_NET_TIMEOUT)


def abort_merge(cwd: str) -> dict[str, Any]:
    """``git merge --abort`` — the way out of a conflicted pull."""
    root, err = _repo_or_fail(cwd)
    if err:
        return err

    def _abort() -> dict[str, Any]:
        code, _out, err_text = _git_write(root, ["merge", "--abort"])
        if code != 0:
            return _fail("abort_failed", err_text.strip() or "git merge --abort failed")
        return _ok()

    return _run_locked(root, _abort)
