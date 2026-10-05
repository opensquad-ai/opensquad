"""What code this process is actually running — and whether it is older than the code beside it.

The failure this exists for: a fix lands, the fleet keeps running the build it started with hours
earlier, and everyone re-diagnoses the same bug from behaviour that was already fixed. A process
cannot notice that by itself unless something tells it the difference, so it stamps itself at
startup — version, commit, and which source tree it loaded — and compares its own start time against
the source files underneath it. A file newer than the process is a change this process has not read.

Everything here is best-effort: an installed build with no git checkout still reports its version,
and a process that cannot say more says less rather than failing.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path(__file__).resolve().parent

_SKIP_DIRS = {"__pycache__", "node_modules", ".git", "build", "dist"}


def _git(args: list[str], cwd: Path) -> str:
    try:
        from opensquad.proc_text import utf8_text_kwargs

        out = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            timeout=5,
            check=False,
            **utf8_text_kwargs(),
        )
    except Exception:
        return ""
    return (out.stdout or "").strip() if out.returncode == 0 else ""


@lru_cache(maxsize=1)
def commit() -> str:
    return _git(["rev-parse", "--short", "HEAD"], SOURCE_ROOT)


@lru_cache(maxsize=1)
def commit_time() -> str:
    return _git(["show", "-s", "--format=%cI", "HEAD"], SOURCE_ROOT)


@lru_cache(maxsize=1)
def version() -> str:
    try:
        from importlib.metadata import version as _installed

        found = str(_installed("opensquad") or "")
        if found:
            return found
    except Exception:
        pass
    try:
        from opensquad import __version__

        return str(__version__)
    except Exception:
        return ""


@lru_cache(maxsize=1)
def describe() -> str:
    parts = [f"opensquad {version() or 'unknown'}"]
    if commit():
        parts.append(commit())
    if commit_time():
        parts.append(commit_time())
    parts.append(f"src={SOURCE_ROOT}")
    # ASCII separator: these lines go to a Windows console whose code page mangles anything else.
    return " | ".join(parts)


def newest_source_change(
    since: float | None = None,
    root: Path | None = None,
) -> tuple[str, float] | None:
    """The most recently modified .py under the package, newer than ``since`` when given."""
    newest: tuple[str, float] | None = None
    base = root or SOURCE_ROOT
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if since is not None and mtime <= since:
                continue
            if newest is None or mtime > newest[1]:
                newest = (path, mtime)
    return newest


def staleness(*, started_at: float | None = None, root: Path | None = None) -> str:
    """A sentence to log when this process is older than the code beside it, else ``""``.

    ``started_at`` defaults to import time, which is the moment this process read the source it is
    running: any file with a newer mtime is a change it never saw.
    """
    stamp = PROCESS_STARTED_AT if started_at is None else started_at
    fresh = newest_source_change(since=stamp, root=root)
    if not fresh:
        return ""
    path, mtime = fresh
    try:
        where = os.path.relpath(path, root or SOURCE_ROOT)
    except Exception:
        where = path
    return (
        f"running the older code: this process started "
        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stamp))} but {where} changed at "
        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime))} — restart it to pick the "
        "change up"
    )


def snapshot() -> dict[str, Any]:
    """Everything worth reporting about this process's code, including staleness."""
    return {
        "version": version(),
        "commit": commit(),
        "commit_time": commit_time(),
        "source_root": str(SOURCE_ROOT),
        "started_at": PROCESS_STARTED_AT,
        "stale": staleness(),
    }


def log_build(logger: logging.Logger, *, what: str = "process") -> None:
    """Log the build stamp, and make a stale one loud rather than merely present."""
    logger.info("[Build] %s: %s", what, describe())
    behind = staleness()
    if behind:
        logger.error("[Build] %s is %s", what, behind)


PROCESS_STARTED_AT = time.time()
