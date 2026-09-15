"""Session-scoped project file change tracking (directory checkpoints + incremental stats).

Layout under ``{project}/.opensquad/session_changes/``::

    meta.json                 # index + per-file +/- stats (no file bodies)
    baseline/<relpath>        # pre-mutation snapshots (or absent + state=missing)
    ckpt/<message_id>/        # dirty-tree snapshot at user-send
      manifest.json
      <relpath>…

Independent of Git. Cleared on Accept / new_session.

Two invariants keep the Changes panel honest — every judgment (list membership,
per-file ``+/-``, the rendered diff) is measured against the same peer:

1. **The Accept baseline is that peer.** It is also exactly what Revert /
   withdraw restores, so "listed + this many lines" can never disagree with the
   diff the user opens. A per-edit snapshot (``edit_base``, v2) used to back the
   panel instead; because the shell watcher re-froze it to the current disk
   before every CMD that ran, every re-scanned path reported ``+0/-0`` while
   still being listed, and the opened diff was empty.
2. **Path keys are canonical.** Every stored/looked-up key goes through
   :func:`_canon_key` (same folding as :func:`_norm_rel`, i.e. ``normcase`` on
   Windows). Git returns paths in their real casing while the tools fold theirs,
   so writing one case and looking up another produced a second, unfetchable
   row ("no diff" ghosts).
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import threading
from typing import Any

_MAX_BASELINE_BYTES = 2_000_000
_lock = threading.RLock()
_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _norm_rel(root: str, abs_or_rel: str) -> str | None:
    root_abs = os.path.normcase(os.path.abspath(root))
    raw = (abs_or_rel or "").strip()
    if not raw:
        return None
    if os.path.isabs(raw):
        abs_path = os.path.normcase(os.path.abspath(raw))
    else:
        abs_path = os.path.normcase(os.path.abspath(os.path.join(root_abs, raw)))
    try:
        if os.path.commonpath([root_abs, abs_path]) != root_abs:
            return None
    except Exception:
        return None
    rel = os.path.relpath(abs_path, root_abs).replace("\\", "/")
    if rel.startswith("..") or rel in (".", ""):
        return None
    return rel


def _abs(root: str, rel: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.join(root, rel.replace("/", os.sep))))


def _canon_key(rel: str) -> str:
    """Canonical store/route key for an already-relative path.

    Single source of truth for path keys: the same folding :func:`_norm_rel`
    produces (``os.path.normcase`` on an absolute path, then ``/`` separators),
    applied here to a relative path. Producers that bypass ``_norm_rel`` — most
    notably git porcelain output, which keeps the on-disk casing — must fold
    through this helper, otherwise the row is stored under ``src/Foo.py`` while
    every lookup asks for ``src/foo.py`` and the row becomes unfetchable.
    """
    return os.path.normcase((rel or "").strip()).replace("\\", "/")


# meta.json fields keyed by project-relative path
_PATH_KEYED_FIELDS = ("baseline", "file_stats", "created", "kept")


def _canonicalize_meta(meta: dict[str, Any]) -> None:
    """Fold legacy mixed-case path keys onto canonical ones in place.

    Canonical key wins when a store holds both spellings of the same path; the
    legacy duplicate is dropped so the path is listed (and diffed) exactly once.
    """
    for field in _PATH_KEYED_FIELDS:
        mapping = meta.get(field)
        if not isinstance(mapping, dict) or not mapping:
            continue
        if all(_canon_key(k) == k for k in mapping):
            continue
        folded: dict[str, Any] = {}
        for key, value in mapping.items():
            canon = _canon_key(key)
            if canon in folded and key != canon:
                continue  # a canonical entry already won
            folded[canon] = value
        meta[field] = folded


def _changes_root(root: str) -> str:
    return os.path.join(os.path.normcase(os.path.abspath(root)), ".opensquad", "session_changes")


def _meta_path(root: str) -> str:
    return os.path.join(_changes_root(root), "meta.json")


def _safe_message_id(message_id: str) -> str:
    mid = _SAFE_ID_RE.sub("_", (message_id or "").strip())[:120]
    return mid or "unknown"


def _blob_path(bucket_dir: str, rel: str) -> str | None:
    """Resolve ``bucket_dir/rel`` ensuring it stays under *bucket_dir*."""
    bucket = os.path.normcase(os.path.abspath(bucket_dir))
    candidate = os.path.normcase(os.path.abspath(os.path.join(bucket, rel.replace("/", os.sep))))
    try:
        if os.path.commonpath([bucket, candidate]) != bucket:
            return None
    except Exception:
        return None
    return candidate


def _empty_meta() -> dict[str, Any]:
    return {
        "version": 3,
        # path -> "file" | "missing" | "oversized"  (Accept / revert point — the
        # single peer behind list membership, +/- stats and the rendered diff)
        "baseline": {},
        # path -> {additions, deletions, status}
        "file_stats": {},
        # path -> True if file was created this session (revert should delete)
        "created": {},
        # path -> {mtime, size} — user kept/saved; hidden from Changes until mutated again
        "kept": {},
        "checkpoint_order": [],
    }


def _load_meta(root: str) -> dict[str, Any]:
    path = _meta_path(root)
    if not os.path.isfile(path):
        # Migrate away from legacy single JSON if present
        legacy = os.path.join(os.path.dirname(_changes_root(root)), "session_changeset.json")
        if os.path.isfile(legacy):
            try:
                os.remove(legacy)
            except Exception:
                pass
        return _empty_meta()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty_meta()
        data.setdefault("version", 3)
        data.setdefault("baseline", {})
        data.setdefault("file_stats", {})
        data.setdefault("created", {})
        data.setdefault("kept", {})
        data.setdefault("checkpoint_order", [])
        # v2 stored a per-edit snapshot (edit_base) that backed the panel diff.
        # The panel is baseline-relative now, so drop the store and its blobs.
        if "edit_base" in data:
            data.pop("edit_base", None)
            if int(data.get("version") or 2) < 3:
                shutil.rmtree(_edit_base_dir(root), ignore_errors=True)
            data["version"] = 3
        _canonicalize_meta(data)
        return data
    except Exception:
        return _empty_meta()


def _save_meta(root: str, data: dict[str, Any]) -> None:
    cr = _changes_root(root)
    os.makedirs(cr, exist_ok=True)
    path = _meta_path(root)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0)
    os.replace(tmp, path)


def _read_text(abs_path: str) -> tuple[str | None, bool]:
    if not os.path.isfile(abs_path):
        return None, False
    try:
        size = os.path.getsize(abs_path)
        if size > _MAX_BASELINE_BYTES:
            return None, True
        with open(abs_path, encoding="utf-8-sig", errors="replace") as f:
            return f.read(), False
    except Exception:
        return None, False


def _write_text(abs_path: str, content: str | None) -> None:
    if content is None:
        if os.path.isfile(abs_path):
            os.remove(abs_path)
        return
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)


def _line_stats(old: str | None, new: str | None) -> tuple[int, int]:
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    if old is None and new is None:
        return 0, 0
    if old is None:
        return len(new_lines), 0
    if new is None:
        return 0, len(old_lines)
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    additions = deletions = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            additions += j2 - j1
        elif tag == "delete":
            deletions += i2 - i1
        elif tag == "replace":
            deletions += i2 - i1
            additions += j2 - j1
    return additions, deletions


def _baseline_dir(root: str) -> str:
    return os.path.join(_changes_root(root), "baseline")


def _edit_base_dir(root: str) -> str:
    """Legacy (v2) per-edit snapshot dir — only referenced by the v3 migration."""
    return os.path.join(_changes_root(root), "edit_base")


def _ckpt_dir(root: str, message_id: str) -> str:
    return os.path.join(_changes_root(root), "ckpt", _safe_message_id(message_id))


def _read_snap_content(root: str, rel: str, meta: dict[str, Any]) -> tuple[str | None, bool]:
    """Read the Accept baseline body for *rel*. content None = missing."""
    state = (meta.get("baseline") or {}).get(rel)
    if state == "oversized":
        return None, True
    if state == "missing":
        return None, False
    if state != "file":
        return None, False
    blob = _blob_path(_baseline_dir(root), rel)
    if not blob or not os.path.isfile(blob):
        return None, False
    content, oversized = _read_text(blob)
    if oversized:
        return None, True
    return content, False


def _read_baseline_content(root: str, rel: str, meta: dict[str, Any]) -> tuple[str | None, bool]:
    """Accept/revert snapshot — the peer for stats, diff and Revert alike."""
    return _read_snap_content(root, rel, meta)


def _write_baseline_blob(root: str, rel: str, content: str | None, *, oversized: bool) -> str:
    """Persist baseline body; return state label."""
    if oversized:
        return "oversized"
    if content is None:
        return "missing"
    blob = _blob_path(_baseline_dir(root), rel)
    if not blob:
        return "oversized"
    os.makedirs(os.path.dirname(blob), exist_ok=True)
    with open(blob, "w", encoding="utf-8") as f:
        f.write(content)
    return "file"


def _recompute_file_stat(root: str, rel: str, meta: dict[str, Any]) -> None:
    accept_old, accept_over = _read_baseline_content(root, rel, meta)
    abs_path = _abs(root, rel)
    exists = os.path.isfile(abs_path)
    mtime = 0.0
    size = 0
    if exists:
        try:
            stt = os.stat(abs_path)
            mtime = float(stt.st_mtime)
            size = int(stt.st_size)
        except Exception:
            pass
    created = bool((meta.get("created") or {}).get(rel))
    oversized = bool(accept_over or (exists and size > _MAX_BASELINE_BYTES))

    # User kept this file: hide from Changes until disk changes or a new tool edit
    kept_info = (meta.get("kept") or {}).get(rel)
    if kept_info is not None:
        still_kept = False
        if isinstance(kept_info, dict):
            if (not exists and kept_info.get("missing")) or (
                exists
                and abs(float(kept_info.get("mtime") or 0) - mtime) < 1e-6
                and int(kept_info.get("size") or -1) == size
            ):
                still_kept = True
        elif kept_info:
            still_kept = True
        if still_kept:
            meta["file_stats"].pop(rel, None)
            return
        # Disk changed after keep → show in Changes again
        meta.get("kept", {}).pop(rel, None)

    if not exists:
        new = None
    else:
        new, over = _read_text(abs_path)
        if over:
            oversized = True
            new = None

    # Fully restored to Accept → drop tracking
    if not created and accept_old == new and not oversized:
        meta["file_stats"].pop(rel, None)
        meta["baseline"].pop(rel, None)
        meta.get("created", {}).pop(rel, None)
        meta.get("kept", {}).pop(rel, None)
        blob = _blob_path(_baseline_dir(root), rel)
        if blob and os.path.isfile(blob):
            try:
                os.remove(blob)
            except Exception:
                pass
        return

    if oversized:
        # No line data, but the status must still be read off the baseline: an
        # "oversized" baseline row means the file existed at Accept time, so the
        # change is a modification — not an addition.
        base_state = (meta.get("baseline") or {}).get(rel)
        if created and exists:
            status = "A"
        elif not exists:
            status = "D"
        elif base_state in (None, "missing"):
            status = "A"
        else:
            status = "M"
        meta["file_stats"][rel] = {
            "additions": 0,
            "deletions": 0,
            "status": status,
            "oversized": True,
            "mtime": mtime,
            "size": size,
        }
        return

    # UI +/- measured against the Accept baseline: the same peer Revert restores and
    # the same peer diff_file renders, so a listed path always has a non-empty diff.
    add, dele = _line_stats(accept_old, new)
    if created:
        status = "A" if exists else "D"
    elif accept_old is None and exists:
        status = "A"
    elif not exists:
        status = "D"
    else:
        status = "M"
    meta["file_stats"][rel] = {
        "additions": add,
        "deletions": dele,
        "status": status,
        "mtime": mtime,
        "size": size,
    }


def ensure_baseline_before_write(root: str, path: str) -> str | None:
    """Capture the Accept baseline (once per path) before mutating.

    Returns the canonical relative path, or None if *path* is outside *root*.
    """
    rel = _norm_rel(root, path)
    if not rel:
        return None
    with _lock:
        meta = _load_meta(root)
        meta.setdefault("created", {})
        meta.setdefault("kept", {})
        abs_path = _abs(root, rel)
        # New edit after Keep → show in Changes again
        meta["kept"].pop(rel, None)
        if rel not in meta["baseline"]:
            content, oversized = _read_text(abs_path)
            state = _write_baseline_blob(root, rel, content, oversized=oversized)
            meta["baseline"][rel] = state
            if state == "missing":
                meta["created"][rel] = True
        # Heal legacy rows that never set created for baseline=missing
        if meta["baseline"].get(rel) == "missing":
            meta["created"][rel] = True
        _save_meta(root, meta)
    return rel


def note_deleted(root: str, path: str) -> str | None:
    return ensure_baseline_before_write(root, path)


def note_after_mutation(root: str, path: str) -> dict[str, Any]:
    """After a successful write/delete: refresh per-file +/- in meta (incremental)."""
    rel = _norm_rel(root, path)
    if not rel:
        return {}
    with _lock:
        meta = _load_meta(root)
        if rel not in meta["baseline"]:
            # Should have been baselined before write; best-effort capture as missing
            meta["baseline"][rel] = "missing"
            meta.setdefault("created", {})[rel] = True
        _recompute_file_stat(root, rel, meta)
        _save_meta(root, meta)
        st = meta["file_stats"].get(rel) or {}
        return {
            "path": rel,
            "additions": int(st.get("additions") or 0),
            "deletions": int(st.get("deletions") or 0),
            "status": st.get("status") or "M",
        }


def checkpoint(root: str, message_id: str) -> dict[str, Any]:
    """Snapshot current dirty files at user-send time into ``ckpt/<id>/``."""
    mid = (message_id or "").strip()
    if not mid:
        return {"ok": False, "error": "message_id required"}
    with _lock:
        meta = _load_meta(root)
        tracked = set(meta["baseline"].keys()) | set(meta["file_stats"].keys())
        ckpt = _ckpt_dir(root, mid)
        if os.path.isdir(ckpt):
            shutil.rmtree(ckpt, ignore_errors=True)
        os.makedirs(ckpt, exist_ok=True)
        manifest: dict[str, Any] = {}
        for rel in sorted(tracked):
            abs_path = _abs(root, rel)
            if not os.path.isfile(abs_path):
                manifest[rel] = {"state": "missing"}
                continue
            content, oversized = _read_text(abs_path)
            if oversized:
                manifest[rel] = {"state": "oversized"}
                continue
            blob = _blob_path(ckpt, rel)
            if not blob:
                manifest[rel] = {"state": "oversized"}
                continue
            os.makedirs(os.path.dirname(blob), exist_ok=True)
            with open(blob, "w", encoding="utf-8") as f:
                f.write(content or "")
            manifest[rel] = {"state": "file"}
        with open(os.path.join(ckpt, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False)
        order = [x for x in meta["checkpoint_order"] if _safe_message_id(x) != _safe_message_id(mid)]
        order.append(mid)
        meta["checkpoint_order"] = order
        _save_meta(root, meta)
        return {"ok": True, "message_id": mid, "files": len(manifest)}


def _clear_store(root: str) -> None:
    cr = _changes_root(root)
    if os.path.isdir(cr):
        shutil.rmtree(cr, ignore_errors=True)
    legacy = os.path.join(os.path.dirname(cr), "session_changeset.json")
    if os.path.isfile(legacy):
        try:
            os.remove(legacy)
        except Exception:
            pass


def accept_reset(root: str) -> dict[str, Any]:
    """Accept current disk state: wipe session change store."""
    with _lock:
        _clear_store(root)
        return {"ok": True, "additions": 0, "deletions": 0, "files": [], "count": 0}


def clear_for_new_session(root: str) -> dict[str, Any]:
    """Drop short-lived checkpoints when starting a new chat session."""
    return accept_reset(root)


def summary(root: str) -> dict[str, Any]:
    with _lock:
        meta = _load_meta(root)
        # Heal legacy rows: baseline=missing but created flag never set
        for rel, state in list((meta.get("baseline") or {}).items()):
            if state == "missing":
                meta.setdefault("created", {})[rel] = True
        # Membership is baseline-relative: a stat row with no baseline has no peer to
        # diff or revert against, so it can only ever render as a phantom entry.
        for rel in list(meta.get("file_stats") or {}):
            if rel not in meta["baseline"]:
                meta["file_stats"].pop(rel, None)
        # Always recompute vs disk so Shell/CMD/external edits show up after refresh
        for rel in list(meta["baseline"].keys()):
            _recompute_file_stat(root, rel, meta)
        files: list[dict[str, Any]] = []
        total_add = total_del = 0
        for rel, st in sorted((meta.get("file_stats") or {}).items()):
            add = int(st.get("additions") or 0)
            dele = int(st.get("deletions") or 0)
            total_add += add
            total_del += dele
            files.append(
                {
                    "path": rel,
                    "name": os.path.basename(rel) or rel,
                    "type": "file",
                    "status": st.get("status") or "M",
                    "additions": add,
                    "deletions": dele,
                    "oversized": bool(st.get("oversized")),
                    "mtime": float(st.get("mtime") or 0),
                    "size": int(st.get("size") or 0),
                    "created": bool((meta.get("created") or {}).get(rel)),
                    # Missing on disk (e.g. created then withdrawn) — UI shows red tombstone
                    "missing": not os.path.isfile(_abs(root, rel)),
                }
            )
        _save_meta(root, meta)
        return {
            "cwd": os.path.normcase(os.path.abspath(root)),
            "additions": total_add,
            "deletions": total_del,
            "count": len(files),
            "files": files,
            "entries": files,
        }


def _build_diff_lines(old: str | None, new: str | None, *, collapse: bool = True) -> list[dict[str, Any]]:
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    lines: list[dict[str, Any]] = []
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    old_no = new_no = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            chunk = old_lines[i1:i2]
            # Full context (All Files preview): never fold equal spans.
            if not collapse or len(chunk) <= 8:
                for i, text in enumerate(chunk):
                    lines.append(
                        {
                            "type": "context",
                            "old_lineno": old_no + i + 1,
                            "new_lineno": new_no + i + 1,
                            "text": text,
                        }
                    )
            else:
                for i, text in enumerate(chunk[:3]):
                    lines.append(
                        {
                            "type": "context",
                            "old_lineno": old_no + i + 1,
                            "new_lineno": new_no + i + 1,
                            "text": text,
                        }
                    )
                middle = chunk[3:-3]
                if middle:
                    lines.append(
                        {
                            "type": "collapse",
                            "count": len(middle),
                            "text": f"{len(middle)} unmodified lines",
                            # Frontend can expand these into context rows.
                            "hidden": [
                                {
                                    "type": "context",
                                    "old_lineno": old_no + 3 + i + 1,
                                    "new_lineno": new_no + 3 + i + 1,
                                    "text": text,
                                }
                                for i, text in enumerate(middle)
                            ],
                        }
                    )
                for i, text in enumerate(chunk[-3:]):
                    off = len(chunk) - 3 + i
                    lines.append(
                        {
                            "type": "context",
                            "old_lineno": old_no + off + 1,
                            "new_lineno": new_no + off + 1,
                            "text": text,
                        }
                    )
            old_no += i2 - i1
            new_no += j2 - j1
        elif tag == "delete":
            for i, text in enumerate(old_lines[i1:i2]):
                lines.append({"type": "delete", "old_lineno": old_no + i + 1, "new_lineno": None, "text": text})
            old_no += i2 - i1
        elif tag == "insert":
            for i, text in enumerate(new_lines[j1:j2]):
                lines.append({"type": "insert", "old_lineno": None, "new_lineno": new_no + i + 1, "text": text})
            new_no += j2 - j1
        elif tag == "replace":
            for i, text in enumerate(old_lines[i1:i2]):
                lines.append({"type": "delete", "old_lineno": old_no + i + 1, "new_lineno": None, "text": text})
            old_no += i2 - i1
            for i, text in enumerate(new_lines[j1:j2]):
                lines.append({"type": "insert", "old_lineno": None, "new_lineno": new_no + i + 1, "text": text})
            new_no += j2 - j1
    return lines


def _diff_file_unlocked(root: str, rel: str, meta: dict[str, Any], *, collapse: bool = True) -> dict[str, Any]:
    """Compute one file diff; caller must hold ``_lock``.

    Diff is against the **Accept baseline** — the peer Revert restores and the
    peer the ``+/-`` stats are counted from, matching the ``fs/session-diff``
    contract ("baseline vs disk"). Keeping all three views on one peer is what
    guarantees a listed path can never open an empty diff.
    """
    if rel not in meta["baseline"] and rel not in meta.get("file_stats", {}):
        return {"error": "Path not in session changes", "status": 404, "path": rel}
    old, oversized = _read_baseline_content(root, rel, meta)
    if oversized:
        return {
            "path": rel,
            "oversized": True,
            "additions": 0,
            "deletions": 0,
            "lines": [],
            "status": "M",
        }
    abs_path = _abs(root, rel)
    if not os.path.isfile(abs_path):
        new = None
        status = "D"
    else:
        new, over = _read_text(abs_path)
        if over:
            return {
                "path": rel,
                "oversized": True,
                "additions": 0,
                "deletions": 0,
                "lines": [],
                "status": "M",
            }
        status = "A" if old is None else "M"
    if (meta.get("created") or {}).get(rel) and new is not None:
        status = "A"

    add, dele = _line_stats(old, new)
    return {
        "path": rel,
        "status": status,
        "additions": add,
        "deletions": dele,
        "oversized": False,
        "lines": _build_diff_lines(old, new, collapse=collapse),
    }


def diff_file(root: str, path: str, *, collapse: bool = True) -> dict[str, Any]:
    rel = _norm_rel(root, path)
    if not rel:
        return {"error": "Invalid path", "status": 400}
    with _lock:
        meta = _load_meta(root)
        return _diff_file_unlocked(root, rel, meta, collapse=collapse)


_MAX_BATCH_DIFFS = 60


def diff_files_batch(root: str, paths: list[str] | None = None, *, collapse: bool = True) -> dict[str, Any]:
    """Return diffs for many session-changed paths in one lock/IO pass."""
    with _lock:
        meta = _load_meta(root)
        if paths:
            rels: list[str] = []
            for p in paths:
                rel = _norm_rel(root, p)
                if rel:
                    rels.append(rel)
        else:
            rels = sorted(set(meta.get("file_stats") or {}) | set(meta.get("baseline") or {}))
        if len(rels) > _MAX_BATCH_DIFFS:
            rels = rels[:_MAX_BATCH_DIFFS]
        files: dict[str, Any] = {}
        for rel in rels:
            result = _diff_file_unlocked(root, rel, meta, collapse=collapse)
            if "error" in result:
                continue
            files[rel] = result
        return {"count": len(files), "files": files}


def _load_ckpt_manifest(root: str, message_id: str) -> dict[str, Any] | None:
    ckpt = _ckpt_dir(root, message_id)
    man = os.path.join(ckpt, "manifest.json")
    if not os.path.isfile(man):
        return None
    try:
        with open(man, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        # Legacy manifests were keyed by whatever spelling the writer saw (git
        # porcelain keeps on-disk casing) — fold them like every other path key.
        return {_canon_key(k): v for k, v in data.items()}
    except Exception:
        return None


def _read_ckpt_content(root: str, message_id: str, rel: str, entry: dict[str, Any]) -> tuple[str | None, bool]:
    state = entry.get("state")
    if state == "oversized":
        return None, True
    if state == "missing":
        return None, False
    blob = _blob_path(_ckpt_dir(root, message_id), rel)
    if not blob or not os.path.isfile(blob):
        return None, False
    return _read_text(blob)


def revert_to_checkpoint(root: str, message_id: str) -> dict[str, Any]:
    mid = (message_id or "").strip()
    if not mid:
        return {"ok": False, "error": "message_id required"}
    with _lock:
        meta = _load_meta(root)
        manifest = _load_ckpt_manifest(root, mid)
        if manifest is None:
            return _revert_all_locked(root, meta)

        restored: list[str] = []
        skipped: list[str] = []
        all_paths = (
            set(meta["baseline"].keys())
            | set(meta.get("file_stats", {}).keys())
            | set((meta.get("kept") or {}).keys())
            | set(manifest.keys())
        )

        for rel in all_paths:
            abs_path = _abs(root, rel)
            if rel in manifest:
                content, oversized = _read_ckpt_content(root, mid, rel, manifest[rel] or {})
                if oversized:
                    skipped.append(rel)
                    continue
                try:
                    _write_text(abs_path, content)
                    restored.append(rel)
                except Exception:
                    skipped.append(rel)
            else:
                # Touched only after checkpoint → restore commit baseline
                old, oversized = _read_baseline_content(root, rel, meta)
                if oversized:
                    skipped.append(rel)
                    continue
                try:
                    _write_text(abs_path, old)
                    restored.append(rel)
                except Exception:
                    skipped.append(rel)

        # Drop this and later checkpoints
        order = list(meta["checkpoint_order"])
        safe_mid = _safe_message_id(mid)
        idx = next((i for i, x in enumerate(order) if _safe_message_id(x) == safe_mid), len(order))
        keep_ids = order[:idx]
        for drop_id in order[idx:]:
            d = _ckpt_dir(root, drop_id)
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        meta["checkpoint_order"] = keep_ids

        # Refresh stats vs baseline after restore-to-checkpoint
        meta.setdefault("kept", {})
        for rel in list(meta.get("kept", {}).keys()):
            meta["kept"].pop(rel, None)
        for rel in list(meta["baseline"].keys()):
            _recompute_file_stat(root, rel, meta)
        _save_meta(root, meta)
        return {
            "ok": True,
            "message_id": mid,
            "restored": restored,
            "skipped": skipped,
            **summary(root),
        }


def _revert_all_locked(root: str, meta: dict[str, Any]) -> dict[str, Any]:
    restored: list[str] = []
    skipped: list[str] = []
    created = meta.get("created") or {}
    for rel, state in list((meta.get("baseline") or {}).items()):
        if state == "oversized":
            skipped.append(rel)
            continue
        abs_path = _abs(root, rel)
        if created.get(rel):
            try:
                _write_text(abs_path, None)
                restored.append(rel)
            except Exception:
                skipped.append(rel)
            continue
        old, oversized = _read_baseline_content(root, rel, meta)
        if oversized:
            skipped.append(rel)
            continue
        try:
            _write_text(abs_path, old)
            restored.append(rel)
        except Exception:
            skipped.append(rel)
    _clear_store(root)
    return {
        "ok": True,
        "restored": restored,
        "skipped": skipped,
        "additions": 0,
        "deletions": 0,
        "files": [],
        "count": 0,
    }


def revert_all(root: str) -> dict[str, Any]:
    with _lock:
        meta = _load_meta(root)
        return _revert_all_locked(root, meta)


def revert_file(root: str, path: str) -> dict[str, Any]:
    """Restore a single path to session baseline and drop it from the dirty set."""
    rel = _norm_rel(root, path)
    if not rel:
        return {"ok": False, "error": "Invalid path"}
    with _lock:
        meta = _load_meta(root)
        if rel not in meta["baseline"] and rel not in meta.get("file_stats", {}):
            return {"ok": False, "error": "Path not in session changes"}
        abs_path = _abs(root, rel)
        if (meta.get("created") or {}).get(rel):
            try:
                _write_text(abs_path, None)
            except Exception as e:
                return {"ok": False, "error": str(e), "path": rel}
        else:
            old, oversized = _read_baseline_content(root, rel, meta)
            if oversized:
                return {"ok": False, "error": "File too large to auto-revert", "path": rel, "oversized": True}
            try:
                _write_text(abs_path, old)
            except Exception as e:
                return {"ok": False, "error": str(e), "path": rel}
        meta["baseline"].pop(rel, None)
        meta["file_stats"].pop(rel, None)
        meta.get("created", {}).pop(rel, None)
        meta.get("kept", {}).pop(rel, None)
        blob = _blob_path(_baseline_dir(root), rel)
        if blob and os.path.isfile(blob):
            try:
                os.remove(blob)
            except Exception:
                pass
        _save_meta(root, meta)
        return {"ok": True, "path": rel, **summary(root)}


def keep_file(root: str, path: str) -> dict[str, Any]:
    """Keep/save current disk for one path: drop from Changes stats, retain baseline for withdraw.

    Disk is left unchanged. Message-checkpoint revert still uses Accept baseline /
    ckpt snapshots, so withdraw can roll the file back even after Keep.
    """
    rel = _norm_rel(root, path)
    if not rel:
        return {"ok": False, "error": "Invalid path"}
    with _lock:
        meta = _load_meta(root)
        if rel not in meta["baseline"] and rel not in meta.get("file_stats", {}):
            return {"ok": False, "error": "Path not in session changes"}
        # Ensure we still have an Accept baseline for future withdraw/revert
        if rel not in meta["baseline"]:
            meta["baseline"][rel] = "missing"
            meta.setdefault("created", {})[rel] = True
        abs_path = _abs(root, rel)
        kept: dict[str, Any]
        if os.path.isfile(abs_path):
            try:
                stt = os.stat(abs_path)
                kept = {"mtime": float(stt.st_mtime), "size": int(stt.st_size)}
            except Exception:
                kept = {"mtime": 0.0, "size": 0}
        else:
            kept = {"missing": True, "mtime": 0.0, "size": 0}
        meta.setdefault("kept", {})[rel] = kept
        meta["file_stats"].pop(rel, None)
        _save_meta(root, meta)
        return {"ok": True, "path": rel, "kept": True, **summary(root)}


def keep_all(root: str) -> dict[str, Any]:
    """Keep every path currently in Changes — same as keep_file for each; withdraw still works."""
    with _lock:
        meta = _load_meta(root)
        paths = sorted(meta.get("file_stats") or {})
        for rel in paths:
            if rel not in meta["baseline"]:
                meta["baseline"][rel] = "missing"
                meta.setdefault("created", {})[rel] = True
            abs_path = _abs(root, rel)
            if os.path.isfile(abs_path):
                try:
                    stt = os.stat(abs_path)
                    kept: dict[str, Any] = {
                        "mtime": float(stt.st_mtime),
                        "size": int(stt.st_size),
                    }
                except Exception:
                    kept = {"mtime": 0.0, "size": 0}
            else:
                kept = {"missing": True, "mtime": 0.0, "size": 0}
            meta.setdefault("kept", {})[rel] = kept
            meta["file_stats"].pop(rel, None)
        _save_meta(root, meta)
        # summary() takes the lock again — release first by returning outside; call unlocked path
    return {"ok": True, "kept": True, "kept_count": len(paths), **summary(root)}


def _git_porcelain_paths(root: str) -> list[str]:
    """Dirty paths reported by git, folded onto canonical store keys.

    Git echoes the on-disk spelling (``src/Foo.py``) while every tool-side key is
    already folded by :func:`_norm_rel` (lowercase on Windows). Returning the raw
    spelling made callers store a second row that no lookup could ever resolve.
    """
    import subprocess

    root_abs = os.path.normcase(os.path.abspath(root))
    if not os.path.exists(os.path.join(root_abs, ".git")):
        return []
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=root_abs,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    paths: list[str] = []
    for line in (proc.stdout or "").splitlines():
        if not line or len(line) < 4:
            continue
        rest = line[3:].strip()
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[-1].strip()
        rest = rest.strip('"').replace("\\", "/")
        if not rest:
            continue
        rel = _norm_rel(root, rest)
        if rel:
            paths.append(rel)
    return paths


def _git_head_content(root: str, rel: str) -> tuple[str | None, bool]:
    """Return (content, exists_in_head). content None + exists False → untracked/new."""
    import subprocess

    root_abs = os.path.normcase(os.path.abspath(root))
    try:
        proc = subprocess.run(
            ["git", "show", f"HEAD:{rel}"],
            cwd=root_abs,
            capture_output=True,
            timeout=15,
        )
    except Exception:
        return None, False
    if proc.returncode != 0:
        return None, False
    raw = proc.stdout or b""
    if len(raw) > _MAX_BASELINE_BYTES:
        return None, True  # exists but oversized — treat as oversized baseline
    try:
        return raw.decode("utf-8-sig", errors="replace"), True
    except Exception:
        return None, True


def prepare_shell_watch(root: str) -> None:
    """Before a shell command: baseline the paths git reports dirty.

    Only paths with no baseline yet are captured — an already-tracked path keeps
    the baseline it was first seen with, which is what keeps Revert/Accept honest.
    """
    porcelain = _git_porcelain_paths(root)
    with _lock:
        meta = _load_meta(root)
        meta.setdefault("created", {})
        for raw in porcelain:
            rel = _norm_rel(root, raw)
            if not rel or rel in meta["baseline"]:
                continue
            abs_path = _abs(root, rel)
            content, oversized = _read_text(abs_path)
            state = _write_baseline_blob(root, rel, content, oversized=oversized)
            meta["baseline"][rel] = state
            if state == "missing":
                meta["created"][rel] = True
        _save_meta(root, meta)


def finish_shell_watch(root: str) -> dict[str, Any]:
    """After a shell command: discover dirties via git and refresh +/- stats."""
    porcelain = _git_porcelain_paths(root)
    with _lock:
        meta = _load_meta(root)
        # Refresh already-tracked paths (may have been edited by shell)
        for rel in list(meta["baseline"].keys()):
            _recompute_file_stat(root, rel, meta)

        for raw in porcelain:
            rel = _norm_rel(root, raw)
            if not rel:
                continue
            if rel in meta["baseline"]:
                _recompute_file_stat(root, rel, meta)
                continue
            # Newly dirty since prepare: use HEAD as pre-shell content when tracked
            head_content, in_head = _git_head_content(root, rel)
            if in_head and head_content is None and os.path.isfile(_abs(root, rel)):
                # oversized in HEAD
                meta["baseline"][rel] = "oversized"
            elif in_head:
                state = _write_baseline_blob(root, rel, head_content, oversized=False)
                meta["baseline"][rel] = state
            else:
                # Untracked / created by shell
                meta["baseline"][rel] = "missing"
                meta.setdefault("created", {})[rel] = True
            _recompute_file_stat(root, rel, meta)

        _save_meta(root, meta)
    return summary(root)
