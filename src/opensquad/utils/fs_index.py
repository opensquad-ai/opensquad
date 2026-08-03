"""
Project file-tree indexing: git-accelerated, TTL-cached, bounded walk.

Design mirrors pi-web's ``/api/file-index``:

1. **Git acceleration** — git repositories are listed with a single
   ``git ls-files --cached --others --exclude-standard -z`` call: the git
   index is prebuilt, so this returns the full tree in milliseconds and
   .gitignore rules apply for free.
2. **Bounded walk fallback** — non-git directories use ``os.scandir`` with
   ``follow_symlinks=False`` (no symlink-loop risk), a depth cap and early
   truncation instead of the previous unbounded ``os.listdir`` + ``isdir``
   recursion (which stat()ed every entry and could loop through symlinks).
3. **TTL cache per root** — repeated panel opens / soft refreshes hit the
   in-memory cache instead of rescanning the filesystem.

Output shape is compatible with ``project_fs.list_tree`` (``entries`` /
``count`` / ``truncated`` / ``max_entries`` / ``skipped``) plus two extras:
``has_more`` (a depth-filtered response has deeper content available) and
``max_depth`` (the depth filter applied, ``None`` = full listing).
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any

# Same heavy-dir skip list as project_fs.list_tree (used by the walk fallback
# and to filter git listings for repos that do not gitignore these).
_TREE_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".next",
        ".turbo",
        ".cache",
        "dist",
        "build",
        "coverage",
        "target",
        ".idea",
        ".vs",
    }
)

_MAX_TREE_ENTRIES = 10_000
_WALK_MAX_DEPTH = 12  # walk fallback depth cap (UI rarely needs deeper)
_GIT_HARD_CAP = 200_000  # in-memory index cap before depth/entry filtering
_GIT_TIMEOUT_S = 10.0
_GIT_MAX_BUFFER = 64 * 1024 * 1024

# TTL cache: key=(normcase root, max_entries) -> (expires_at, result)
_CACHE_TTL_S = 10.0
_CACHE_MAX_ENTRIES = 20
_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}


def _cache_key(root: str, max_entries: int) -> tuple[str, int]:
    return (os.path.normcase(os.path.abspath(root)), max_entries)


def _cache_get(root: str, max_entries: int) -> dict[str, Any] | None:
    key = _cache_key(root, max_entries)
    entry = _CACHE.get(key)
    if entry is None:
        return None
    expires_at, result = entry
    if expires_at <= time.monotonic():
        _CACHE.pop(key, None)
        return None
    return result


def _cache_set(root: str, max_entries: int, result: dict[str, Any]) -> None:
    key = _cache_key(root, max_entries)
    now = time.monotonic()
    # Lazy eviction of expired entries; clear-all when over the cap.
    if len(_CACHE) >= _CACHE_MAX_ENTRIES:
        expired = [k for k, (exp, _) in _CACHE.items() if exp <= now]
        for k in expired:
            _CACHE.pop(k, None)
        if len(_CACHE) >= _CACHE_MAX_ENTRIES:
            _CACHE.clear()
    _CACHE[key] = (now + _CACHE_TTL_S, result)


def cache_clear(root: str | None = None) -> None:
    """Drop the cache (all roots, or a single root). Used by tests / file watchers."""
    if root is None:
        _CACHE.clear()
        return
    key_root = os.path.normcase(os.path.abspath(root))
    for key in [k for k in _CACHE if k[0] == key_root]:
        _CACHE.pop(key, None)


# ── Git listing ───────────────────────────────────────────────────────────


def _list_with_git(root: str) -> list[str] | None:
    """Full relative file list via git ls-files; None when not a git repo."""
    try:
        proc = subprocess.run(
            ["git", "-C", root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    files = [f for f in proc.stdout.decode("utf-8", errors="replace").split("\0") if f]
    if len(files) > _GIT_HARD_CAP:
        files = files[:_GIT_HARD_CAP]
    return files


def _path_segments(rel: str) -> list[str]:
    return rel.replace("\\", "/").split("/")


def _is_skipped_path(rel: str) -> str | None:
    """Return the top-level skipped dir name when any segment is heavy."""
    for seg in _path_segments(rel):
        if seg in _TREE_SKIP_DIRS:
            return seg
    return None


def _entries_from_git_files(
    files: list[str], *, max_entries: int, max_depth: int | None, skip_heavy: bool
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    """Build flat entries (files + inferred dirs) from a git file list.

    The UI builds its tree from a flat list, so parent directories of every
    retained file are materialized as ``type: dir`` entries.
    """
    dir_set: set[str] = set()
    file_entries: list[dict[str, Any]] = []
    skipped: list[str] = []
    hard_truncated = False

    for rel in files:
        if skip_heavy:
            heavy = _is_skipped_path(rel)
            if heavy is not None:
                if heavy not in skipped:
                    skipped.append(heavy)
                continue
        segments = _path_segments(rel)
        if max_depth is not None and len(segments) > max_depth:
            hard_truncated = True
            continue
        if len(file_entries) >= max_entries:
            hard_truncated = True
            break
        file_entries.append({"path": rel, "name": segments[-1], "type": "file", "size": None})
        # Materialize parent dirs (all depths, even beyond max_depth, so the
        # parent chain of a depth-limited file still renders).
        for i in range(1, len(segments)):
            dir_set.add("/".join(segments[:i]))

    dir_entries = [
        {"path": d, "name": d.split("/")[-1], "type": "dir", "size": None} for d in sorted(dir_set, key=str.lower)
    ]
    entries = dir_entries + file_entries
    entries.sort(key=lambda e: (e["type"] != "dir", e["path"].lower()))
    return entries, hard_truncated, skipped


# ── Bounded walk fallback ─────────────────────────────────────────────────


def _walk_tree(
    root: str, *, max_entries: int, max_depth: int | None, skip_heavy: bool
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    """Bounded DFS with os.scandir; symlinks are never recursed into."""
    entries: list[dict[str, Any]] = []
    skipped: list[str] = []
    truncated = False
    depth_limit = max_depth if max_depth is not None else _WALK_MAX_DEPTH

    # Stack DFS: (abs_dir, rel_dir, depth). Dirs are sorted for deterministic output.
    stack: list[tuple[str, str, int]] = [(root, "", 0)]
    while stack and not truncated:
        abs_dir, rel_dir, depth = stack.pop()
        try:
            with os.scandir(abs_dir) as it:
                dirents = sorted(it, key=lambda d: d.name.lower())
        except OSError:
            continue

        subdirs: list[tuple[str, str, int]] = []
        for dirent in dirents:
            name = dirent.name
            if name in (".", ".."):
                continue
            child_rel = f"{rel_dir}/{name}" if rel_dir else name

            # Symlinks are listed as files and never followed (loop guard).
            if dirent.is_symlink():
                if len(entries) >= max_entries:
                    truncated = True
                    break
                entries.append({"path": child_rel, "name": name, "type": "file", "size": None})
                continue

            try:
                is_dir = dirent.is_dir(follow_symlinks=False)
            except OSError:
                continue

            if is_dir:
                if skip_heavy and name in _TREE_SKIP_DIRS:
                    skipped.append(child_rel)
                    if len(entries) >= max_entries:
                        truncated = True
                        break
                    entries.append({"path": child_rel, "name": name, "type": "dir", "size": None, "skipped": True})
                    continue
                if depth + 1 > depth_limit:
                    # Deeper content exists but is out of scope -> mark has_more.
                    truncated = True
                    if len(entries) < max_entries:
                        entries.append({"path": child_rel, "name": name, "type": "dir", "size": None})
                    continue
                if len(entries) >= max_entries:
                    truncated = True
                    break
                entries.append({"path": child_rel, "name": name, "type": "dir", "size": None})
                subdirs.append((os.path.join(abs_dir, name), child_rel, depth + 1))
            else:
                if len(entries) >= max_entries:
                    truncated = True
                    break
                size = None
                try:
                    size = dirent.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
                entries.append({"path": child_rel, "name": name, "type": "file", "size": size})

        stack.extend(reversed(subdirs))

    return entries, truncated, skipped


# ── Public API ────────────────────────────────────────────────────────────


def list_tree(
    root: str,
    *,
    max_entries: int = _MAX_TREE_ENTRIES,
    skip_heavy: bool = True,
    max_depth: int | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Recursively list project files/dirs as a flat path list.

    Args:
        root: project directory.
        max_entries: cap on returned entries (UI limit).
        skip_heavy: skip node_modules / .venv / ... heavy dirs.
        max_depth: return only entries with at most this many path segments
            (``None`` = full listing). Used by the UI's lazy expansion.
        use_cache: TTL-cached when True (default) -- repeated calls within
            ``_CACHE_TTL_S`` seconds skip the filesystem entirely.

    Returns the ``project_fs.list_tree``-compatible dict, plus ``has_more``
    (True when depth filtering or truncation hid deeper content) and
    ``max_depth`` (echoed filter, None for full).
    """
    root_abs = os.path.normcase(os.path.abspath(root))
    if not os.path.isdir(root_abs):
        return {"error": f"Not a directory: {root}", "status": 404}

    limit = max(1, min(int(max_entries or _MAX_TREE_ENTRIES), _MAX_TREE_ENTRIES))
    # max_depth=0 means "no limit" in URL params; normalize to None.
    depth = max_depth if max_depth and max_depth > 0 else None

    if use_cache:
        cached = _cache_get(root_abs, limit)
        if cached is not None:
            return _apply_depth_filter(cached, limit, depth, root_abs)

    git_files = _list_with_git(root_abs)
    if git_files is not None:
        entries, hard_trunc, skipped = _entries_from_git_files(
            git_files, max_entries=limit, max_depth=None, skip_heavy=skip_heavy
        )
    else:
        entries, hard_trunc, skipped = _walk_tree(
            root_abs, max_entries=limit, max_depth=_WALK_MAX_DEPTH, skip_heavy=skip_heavy
        )

    result = {
        "cwd": root_abs,
        "path": "",
        "absolute": root_abs,
        "entries": entries,
        "count": len(entries),
        "truncated": hard_trunc,
        "max_entries": limit,
        "skipped": skipped[:50],
        "has_more": hard_trunc,
        "max_depth": None,
    }
    if use_cache:
        _cache_set(root_abs, limit, result)
    return _apply_depth_filter(result, limit, depth, root_abs)


def _apply_depth_filter(result: dict[str, Any], limit: int, depth: int | None, root_abs: str) -> dict[str, Any]:
    """Filter a full cached listing down to *depth* segments (no rescan)."""
    if depth is None:
        return result
    entries = result["entries"]
    kept: list[dict[str, Any]] = []
    has_more = bool(result.get("has_more"))
    for e in entries:
        seg_count = len(_path_segments(e["path"]))
        if seg_count <= depth:
            kept.append(e)
        else:
            has_more = True
    return {
        "cwd": root_abs,
        "path": "",
        "absolute": root_abs,
        "entries": kept[:limit],
        "count": len(kept[:limit]),
        "truncated": bool(result.get("truncated")) or len(kept) > limit,
        "max_entries": limit,
        "skipped": result.get("skipped", [])[:50],
        "has_more": has_more,
        "max_depth": depth,
    }
