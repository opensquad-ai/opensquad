"""Regression tests for ``opensquad.utils.session_changeset``.

The Changes panel renders three views of one fact — the file list with its
``+/-``, the diff you open, and what Revert/withdraw restores.  These tests pin
all three to a single peer, the **Accept baseline**:

* a listed path always reports a non-zero ``+/-`` and a non-empty diff
  (the panel used to show ``+0/-0`` with an empty diff for every path that the
  shell watcher had merely re-scanned, because the per-edit ``edit_base`` peer
  was re-frozen to the current disk before each CMD);
* a path whose disk content equals the baseline disappears from Changes, and a
  stat row that has no baseline (nothing to diff or revert against) is healed
  away instead of rendering as a phantom entry;
* path keys fold to one canonical spelling, so a row created from git's casing
  (``src/MixedCase.py``) is the same row a tool path (``src/mixedcase.py``)
  resolves to — previously the second spelling produced an unfetchable ghost.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from opensquad.utils import session_changeset as sc

# normcase lowercases on Windows (case-insensitive filesystem) and is the identity
# on POSIX, where "src/Foo.py" and "src/foo.py" really are two different files.
_CASE_INSENSITIVE = os.path.normcase("A") != "A"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _edit(root: Path, rel: str, text: str) -> None:
    """Mirror what the filesystem tools do around a mutation."""
    sc.ensure_baseline_before_write(str(root), rel)
    _write(root, rel, text)
    sc.note_after_mutation(str(root), rel)


def _rows(root: Path) -> dict[str, dict]:
    return {row["path"]: row for row in sc.summary(str(root))["files"]}


def test_stats_and_diff_survive_a_shell_watch_pass(tmp_path: Path) -> None:
    """A shell pass must not zero out a real change (the +0/-0 phantom)."""
    _write(tmp_path, "src/app.py", "a\nb\nc\n")
    _edit(tmp_path, "src/app.py", "a\nb\nc\nd\ne\nf\n")

    before = _rows(tmp_path)["src/app.py"]
    assert (before["additions"], before["deletions"]) == (3, 0)

    # A CMD runs and changes nothing: prepare/finish used to re-freeze the
    # per-edit peer to the current disk, which collapsed the row to +0/-0.
    sc.prepare_shell_watch(str(tmp_path))
    sc.finish_shell_watch(str(tmp_path))

    after = _rows(tmp_path)["src/app.py"]
    assert (after["additions"], after["deletions"]) == (3, 0)
    diff = sc.diff_file(str(tmp_path), "src/app.py")
    assert (diff["additions"], diff["deletions"]) == (3, 0)
    assert [line for line in diff["lines"] if line["type"] == "insert"]


def test_every_listed_path_has_a_non_empty_diff(tmp_path: Path) -> None:
    """List, diff and Revert must agree for modify / create / delete alike."""
    _write(tmp_path, "src/mod.py", "one\ntwo\n")
    _write(tmp_path, "src/gone.py", "old\nbody\n")
    _edit(tmp_path, "src/mod.py", "one\ntwo\nthree\n")
    _edit(tmp_path, "src/new.py", "fresh\n")
    sc.ensure_baseline_before_write(str(tmp_path), "src/gone.py")
    (tmp_path / "src" / "gone.py").unlink()
    sc.note_after_mutation(str(tmp_path), "src/gone.py")

    rows = _rows(tmp_path)
    assert set(rows) == {"src/mod.py", "src/new.py", "src/gone.py"}
    for rel, row in rows.items():
        diff = sc.diff_file(str(tmp_path), rel)
        assert "error" not in diff, rel
        assert (diff["additions"], diff["deletions"]) == (row["additions"], row["deletions"]), rel
        assert row["additions"] + row["deletions"] > 0, rel
        assert [line for line in diff["lines"] if line["type"] in ("insert", "delete")], rel


def test_path_restored_to_baseline_leaves_changes(tmp_path: Path) -> None:
    original = "keep\nthis\nfile\n"
    _write(tmp_path, "src/restore.py", original)
    _edit(tmp_path, "src/restore.py", "keep\nthis\nfile\nand\nmore\n")
    assert "src/restore.py" in _rows(tmp_path)

    _write(tmp_path, "src/restore.py", original)
    assert _rows(tmp_path) == {}


def test_oversized_baseline_row_is_a_modification(tmp_path: Path) -> None:
    """An oversized baseline means the file existed at Accept time → status M."""
    _write(tmp_path, "src/big.py", "x\n")
    changes = Path(sc._changes_root(str(tmp_path)))
    changes.mkdir(parents=True, exist_ok=True)
    (changes / "meta.json").write_text(
        json.dumps(
            {
                "version": 3,
                "baseline": {"src/big.py": "oversized"},
                "file_stats": {},
                "created": {},
                "kept": {},
                "checkpoint_order": [],
            }
        ),
        encoding="utf-8",
    )
    rows = _rows(tmp_path)
    assert rows["src/big.py"]["status"] == "M"
    assert rows["src/big.py"]["oversized"] is True


def test_stat_row_without_baseline_is_healed_away(tmp_path: Path) -> None:
    """Membership is baseline-relative: no baseline → nothing to show or revert."""
    changes = Path(sc._changes_root(str(tmp_path)))
    changes.mkdir(parents=True, exist_ok=True)
    (changes / "meta.json").write_text(
        json.dumps(
            {
                "version": 3,
                "baseline": {},
                "file_stats": {"src/ghost.py": {"additions": 0, "deletions": 0, "status": "M"}},
                "created": {},
                "kept": {},
                "checkpoint_order": [],
            }
        ),
        encoding="utf-8",
    )
    assert sc.summary(str(tmp_path))["count"] == 0


def test_edit_base_store_is_retired(tmp_path: Path) -> None:
    """v2's per-edit peer must not come back: display is baseline-relative only."""
    assert "edit_base" not in sc._empty_meta()

    _write(tmp_path, "src/app.py", "x\n")
    _edit(tmp_path, "src/app.py", "x\ny\n")
    meta = json.loads(Path(sc._meta_path(str(tmp_path))).read_text(encoding="utf-8"))
    assert "edit_base" not in meta
    assert meta["version"] == 3


def test_legacy_v2_edit_base_is_dropped_on_load(tmp_path: Path) -> None:
    """An existing store heals: the field goes away and its blobs are removed."""
    changes = Path(sc._changes_root(str(tmp_path)))
    blob = changes / "edit_base" / "src" / "app.py"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text("stale\n", encoding="utf-8")
    (changes / "meta.json").write_text(
        json.dumps(
            {
                "version": 2,
                "baseline": {},
                "edit_base": {"src/app.py": "file"},
                "file_stats": {},
                "created": {},
                "kept": {},
                "checkpoint_order": [],
            }
        ),
        encoding="utf-8",
    )
    meta = sc._load_meta(str(tmp_path))
    assert "edit_base" not in meta
    assert not blob.parent.parent.exists()


def test_canon_key_matches_norm_rel_folding(tmp_path: Path) -> None:
    """One folding rule for stored keys and for path validation."""
    for raw in ("src/App.PY", "src\\App.PY", "src/sub/Deep.Tsx"):
        assert sc._canon_key(raw) == sc._norm_rel(str(tmp_path), raw)
        assert "\\" not in sc._canon_key(raw)


@pytest.mark.skipif(not _CASE_INSENSITIVE, reason="case folding is Windows-only")
def test_git_casing_and_tool_casing_are_one_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Git echoes on-disk casing; it must land on the same key the tools use."""
    _write(tmp_path, "src/MixedCase.py", "a\nb\n")
    # What git porcelain reports: the real on-disk spelling.
    monkeypatch.setattr(sc, "_git_porcelain_paths", lambda root: ["src/MixedCase.py"])
    sc.prepare_shell_watch(str(tmp_path))
    _write(tmp_path, "src/MixedCase.py", "a\nb\nc\n")
    sc.finish_shell_watch(str(tmp_path))

    rows = _rows(tmp_path)
    assert list(rows) == [sc._canon_key("src/MixedCase.py")]
    assert rows[sc._canon_key("src/MixedCase.py")]["additions"] == 1
    # The panel routes the path back in its original casing — it must resolve.
    diff = sc.diff_file(str(tmp_path), "src/MixedCase.py")
    assert "error" not in diff
    assert diff["additions"] == 1


@pytest.mark.skipif(not _CASE_INSENSITIVE, reason="case folding is Windows-only")
def test_legacy_mixed_case_meta_folds_to_one_row(tmp_path: Path) -> None:
    """A store written before the fix must not keep two rows for one file."""
    _write(tmp_path, "src/Mixed.py", "a\nb\nc\n")
    changes = Path(sc._changes_root(str(tmp_path)))
    blob = changes / "baseline" / "src" / "Mixed.py"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text("a\nb\n", encoding="utf-8")
    (changes / "meta.json").write_text(
        json.dumps(
            {
                "version": 3,
                "baseline": {"src/Mixed.py": "file"},
                "file_stats": {"src/mixed.py": {"additions": 9, "deletions": 9, "status": "M"}},
                "created": {},
                "kept": {},
                "checkpoint_order": [],
            }
        ),
        encoding="utf-8",
    )

    rows = _rows(tmp_path)
    assert list(rows) == [sc._canon_key("src/Mixed.py")]
    assert (rows[sc._canon_key("src/Mixed.py")]["additions"], rows[sc._canon_key("src/Mixed.py")]["deletions"]) == (
        1,
        0,
    )


@pytest.mark.skipif(not _CASE_INSENSITIVE, reason="case folding is Windows-only")
def test_checkpoint_manifest_keys_are_folded(tmp_path: Path) -> None:
    """Legacy manifests keyed by git casing must still match tracked paths."""
    _write(tmp_path, "src/Ckpt.py", "v1\n")
    sc.ensure_baseline_before_write(str(tmp_path), "src/ckpt.py")
    assert sc.checkpoint(str(tmp_path), "m1")["ok"] is True

    manifest = Path(sc._ckpt_dir(str(tmp_path), "m1")) / "manifest.json"
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    assert list(raw) == [sc._canon_key("src/Ckpt.py")]
    # Rewrite it the way a pre-fix writer would have: git's on-disk casing.
    manifest.write_text(json.dumps({"src/Ckpt.py": raw[sc._canon_key("src/Ckpt.py")]}), encoding="utf-8")

    _write(tmp_path, "src/Ckpt.py", "v2\n")
    result = sc.revert_to_checkpoint(str(tmp_path), "m1")
    assert result["ok"] is True
    assert (tmp_path / "src" / "Ckpt.py").read_text(encoding="utf-8") == "v1\n"
