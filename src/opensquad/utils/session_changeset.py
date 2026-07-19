"""Session-scoped project file change tracking (directory checkpoints + incremental stats).

Layout under ``{project}/.opensquad/session_changes/``::

    meta.json                 # index + per-file +/- stats (no file bodies)
    baseline/<relpath>        # pre-mutation snapshots (or absent + state=missing)
    ckpt/<message_id>/        # dirty-tree snapshot at user-send
      manifest.json
      <relpath>…

Independent of Git. Cleared on Accept / new_session.
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
        "version": 2,
        # path -> "file" | "missing" | "oversized"  (Accept / revert point)
        "baseline": {},
        # path -> "file" | "missing" | "oversized"  (content before latest edit; UI diff)
        "edit_base": {},
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
        data.setdefault("version", 2)
        data.setdefault("baseline", {})
        data.setdefault("edit_base", {})
        data.setdefault("file_stats", {})
        data.setdefault("created", {})
        data.setdefault("kept", {})
        data.setdefault("checkpoint_order", [])
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
    return os.path.join(_changes_root(root), "edit_base")


def _ckpt_dir(root: str, message_id: str) -> str:
    return os.path.join(_changes_root(root), "ckpt", _safe_message_id(message_id))


def _read_snap_content(root: str, rel: str, store: str, meta: dict[str, Any]) -> tuple[str | None, bool]:
    """Read snapshot content from baseline/ or edit_base/. content None = missing."""
    state = (meta.get(store) or {}).get(rel)
    if state == "oversized":
        return None, True
    if state == "missing":
        return None, False
    if state != "file":
        return None, False
    base = _baseline_dir(root) if store == "baseline" else _edit_base_dir(root)
    blob = _blob_path(base, rel)
    if not blob or not os.path.isfile(blob):
        return None, False
    content, oversized = _read_text(blob)
    if oversized:
        return None, True
    return content, False


def _read_baseline_content(root: str, rel: str, meta: dict[str, Any]) -> tuple[str | None, bool]:
    """Accept/revert snapshot."""
    return _read_snap_content(root, rel, "baseline", meta)


def _read_edit_base_content(root: str, rel: str, meta: dict[str, Any]) -> tuple[str | None, bool]:
    """Pre-latest-edit snapshot for UI red/green (falls back to Accept baseline)."""
    if rel in (meta.get("edit_base") or {}):
        return _read_snap_content(root, rel, "edit_base", meta)
    return _read_baseline_content(root, rel, meta)


def _write_snap_blob(root: str, rel: str, content: str | None, *, oversized: bool, store: str) -> str:
    """Persist snapshot body; return state label."""
    if oversized:
        return "oversized"
    if content is None:
        return "missing"
    base = _baseline_dir(root) if store == "baseline" else _edit_base_dir(root)
    blob = _blob_path(base, rel)
    if not blob:
        return "oversized"
    os.makedirs(os.path.dirname(blob), exist_ok=True)
    with open(blob, "w", encoding="utf-8") as f:
        f.write(content)
    return "file"


def _write_baseline_blob(root: str, rel: str, content: str | None, *, oversized: bool) -> str:
    return _write_snap_blob(root, rel, content, oversized=oversized, store="baseline")


def _capture_edit_base_from_disk(root: str, rel: str, meta: dict[str, Any]) -> None:
    """Freeze current disk as the 'before this edit' peer for red/green UI."""
    abs_path = _abs(root, rel)
    content, oversized = _read_text(abs_path)
    state = _write_snap_blob(root, rel, content, oversized=oversized, store="edit_base")
    meta.setdefault("edit_base", {})[rel] = state


def _capture_edit_base_content(root: str, rel: str, meta: dict[str, Any], content: str | None) -> None:
    """Freeze known pre-edit body (avoids racing the upcoming write)."""
    if content is None:
        state = "missing"
    else:
        oversized = len(content.encode("utf-8", errors="replace")) > _MAX_BASELINE_BYTES
        state = _write_snap_blob(root, rel, content, oversized=oversized, store="edit_base")
    meta.setdefault("edit_base", {})[rel] = state


def _recompute_file_stat(root: str, rel: str, meta: dict[str, Any]) -> None:
    accept_old, accept_over = _read_baseline_content(root, rel, meta)
    edit_old, edit_over = _read_edit_base_content(root, rel, meta)
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
    oversized = bool(accept_over or edit_over or (exists and size > _MAX_BASELINE_BYTES))

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
        meta.get("edit_base", {}).pop(rel, None)
        meta.get("created", {}).pop(rel, None)
        meta.get("kept", {}).pop(rel, None)
        for d in (_baseline_dir(root), _edit_base_dir(root)):
            blob = _blob_path(d, rel)
            if blob and os.path.isfile(blob):
                try:
                    os.remove(blob)
                except Exception:
                    pass
        return

    if oversized:
        status = "A" if accept_old is None and exists else ("D" if not exists else "M")
        if created and exists:
            status = "A"
        meta["file_stats"][rel] = {
            "additions": 0,
            "deletions": 0,
            "status": status,
            "oversized": True,
            "mtime": mtime,
            "size": size,
        }
        return

    # UI +/- vs edit_base (before this edit) so replacing prior additions shows red deletes
    add, dele = _line_stats(edit_old, new)
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


_PREV_UNSET = object()


def ensure_baseline_before_write(
    root: str,
    path: str,
    *,
    prev_content: Any = _PREV_UNSET,
) -> str | None:
    """Capture Accept baseline (once) + edit_base (every time) before mutating.

    Returns the normalized relative path, or None if *path* is outside *root*.

    ``prev_content``:
      - omitted: snapshot edit_base from disk
      - ``None``: file did not exist before this edit (edit_base = missing)
      - ``str``: exact pre-edit body (preferred for write/replace tools)
    """
    rel = _norm_rel(root, path)
    if not rel:
        return None
    with _lock:
        meta = _load_meta(root)
        meta.setdefault("created", {})
        meta.setdefault("edit_base", {})
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
        if prev_content is _PREV_UNSET:
            _capture_edit_base_from_disk(root, rel, meta)
        else:
            _capture_edit_base_content(root, rel, meta, prev_content if isinstance(prev_content, str) else None)
        # Heal legacy rows that never set created for baseline=missing
        if meta["baseline"].get(rel) == "missing":
            meta["created"][rel] = True
        _save_meta(root, meta)
    return rel


def note_deleted(root: str, path: str, *, prev_content: Any = _PREV_UNSET) -> str | None:
    return ensure_baseline_before_write(root, path, prev_content=prev_content)


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


def _build_diff_lines(old: str | None, new: str | None) -> list[dict[str, Any]]:
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    lines: list[dict[str, Any]] = []
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    old_no = new_no = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            chunk = old_lines[i1:i2]
            if len(chunk) > 8:
                for i, text in enumerate(chunk[:3]):
                    lines.append(
                        {
                            "type": "context",
                            "old_lineno": old_no + i + 1,
                            "new_lineno": new_no + i + 1,
                            "text": text,
                        }
                    )
                skipped = len(chunk) - 6
                if skipped > 0:
                    lines.append({"type": "collapse", "count": skipped, "text": f"{skipped} unmodified lines"})
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
            else:
                for i, text in enumerate(chunk):
                    lines.append(
                        {
                            "type": "context",
                            "old_lineno": old_no + i + 1,
                            "new_lineno": new_no + i + 1,
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


def _diff_file_unlocked(root: str, rel: str, meta: dict[str, Any]) -> dict[str, Any]:
    """Compute one file diff; caller must hold ``_lock``.

    Diff is against *edit_base* (content before the latest edit) so replacing
    previously-added lines shows as red deletions + green insertions.
    """
    if rel not in meta["baseline"] and rel not in meta.get("file_stats", {}):
        return {"error": "Path not in session changes", "status": 404, "path": rel}
    old, oversized = _read_edit_base_content(root, rel, meta)
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
        "lines": _build_diff_lines(old, new),
    }


def diff_file(root: str, path: str) -> dict[str, Any]:
    rel = _norm_rel(root, path)
    if not rel:
        return {"error": "Invalid path", "status": 400}
    with _lock:
        meta = _load_meta(root)
        return _diff_file_unlocked(root, rel, meta)


_MAX_BATCH_DIFFS = 60


def diff_files_batch(root: str, paths: list[str] | None = None) -> dict[str, Any]:
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
            result = _diff_file_unlocked(root, rel, meta)
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
        return data if isinstance(data, dict) else None
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
        meta.get("edit_base", {}).pop(rel, None)
        meta.get("kept", {}).pop(rel, None)
        for d in (_baseline_dir(root), _edit_base_dir(root)):
            blob = _blob_path(d, rel)
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
        meta.get("edit_base", {}).pop(rel, None)
        eb = _blob_path(_edit_base_dir(root), rel)
        if eb and os.path.isfile(eb):
            try:
                os.remove(eb)
            except Exception:
                pass
        _save_meta(root, meta)
        return {"ok": True, "path": rel, "kept": True, **summary(root)}


def _git_porcelain_paths(root: str) -> list[str]:
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
        if rest:
            paths.append(rest)
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
    """Before a shell command: baseline dirties + freeze edit_base for tracked paths."""
    porcelain = _git_porcelain_paths(root)
    with _lock:
        meta = _load_meta(root)
        meta.setdefault("created", {})
        meta.setdefault("edit_base", {})
        # Freeze current disk as edit peer for already-tracked files
        for rel in list(meta.get("baseline") or {}):
            _capture_edit_base_from_disk(root, rel, meta)
        for rel in porcelain:
            if rel in meta["baseline"]:
                continue
            abs_path = _abs(root, rel)
            content, oversized = _read_text(abs_path)
            state = _write_baseline_blob(root, rel, content, oversized=oversized)
            meta["baseline"][rel] = state
            if state == "missing":
                meta["created"][rel] = True
            _capture_edit_base_from_disk(root, rel, meta)
        _save_meta(root, meta)


def finish_shell_watch(root: str) -> dict[str, Any]:
    """After a shell command: discover dirties via git and refresh +/- stats."""
    porcelain = _git_porcelain_paths(root)
    with _lock:
        meta = _load_meta(root)
        # Refresh already-tracked paths (may have been edited by shell)
        for rel in list(meta["baseline"].keys()):
            _recompute_file_stat(root, rel, meta)

        for rel in porcelain:
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
