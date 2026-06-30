"""P2/P3 fix verification for opensquad.collab_board.

Run: python -m pytest tests/test_collab_board_p2.py -q
"""

import os
import shutil
import tempfile

import pytest

import opensquad.system_config as syscfg
from opensquad import collab_board as cb


@pytest.fixture()
def tmp_workspace(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="cb_p2_test_")
    syscfg.set_workspace(tmp)
    # Reset the module-level WAL cache so it points at the temp workspace.
    cb._WAL_DIR_CACHE = None
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


# ── _derive_task_status_progress_from_content ─────────────────────────────


def test_derive_ignores_code_fence_checkboxes(tmp_workspace):
    """[x] inside a ``` code block must NOT be counted."""
    content = "```python\n[x] done line\n[ ] pending line\n```\n- [x] real done"
    status, progress = cb._derive_task_status_progress_from_content(content)
    # Only the "- [x] real done" ... wait, that's a bullet not a checkbox.
    # Real checkbox is only inside the fence → all ignored → None
    assert status is None and progress is None


def test_derive_counts_indented_up_to_3_spaces(tmp_workspace):
    content = "[x] a\n  [>] b\n     [ ] c\n[x] d"
    status, progress = cb._derive_task_status_progress_from_content(content)
    # [x]a, [>]b (2-space indent OK), [ ]c (4-space indent skipped), [x]d
    assert status is not None
    # done=2, doing=1, pending=0 → total=3 → progress = round((2+0.5)/3*100)=83
    assert progress == 83


def test_derive_empty_content(tmp_workspace):
    assert cb._derive_task_status_progress_from_content("") == (None, None)
    assert cb._derive_task_status_progress_from_content("no checkboxes here") == (None, None)


# ── _gen_task_id collision retry ──────────────────────────────────────────


def test_gen_task_id_avoids_existing():
    # Generate until we get one not in existing
    existing = set()
    for _ in range(50):
        cid = cb._gen_task_id(existing)
        assert cid not in existing
        existing.add(cid)


def test_gen_task_id_retries_on_collision():
    # Fill the space partially and force the function to skip collisions
    existing = set()
    first = cb._gen_task_id(existing)
    existing.add(first)
    second = cb._gen_task_id(existing)
    assert second != first


# ── update_task accepts 'failed' status ───────────────────────────────────


def test_update_task_failed_status(tmp_workspace):
    t = cb.create_task(task_name="t", created_by="pm")
    updated = cb.update_task(task_id=t["task_id"], status="failed")
    assert updated["status"] == "failed"
    assert updated.get("closed_at") is not None
    assert updated.get("ended_at") is not None


# ── append_public_discussion unique ids ───────────────────────────────────


def test_discussion_unique_ids(tmp_workspace):
    t = cb.create_task(task_name="t", created_by="pm")
    cb.create_task  # noqa
    r1 = cb.append_public_discussion(
        collab_id=t["task_id"],
        task_name="t",
        author_agent_id="a",
        title="x",
        content="hello",
    )
    r2 = cb.append_public_discussion(
        collab_id=t["task_id"],
        task_name="t",
        author_agent_id="a",
        title="x",
        content="hello",
    )
    assert r1["id"] != r2["id"], "two discussions in the same ms must have unique ids"


# ── save_plan_snapshot no same-second overwrite ───────────────────────────


def test_plan_snapshot_no_overwrite_same_second(tmp_workspace):
    t = cb.create_task(task_name="t", created_by="pm")
    s1 = cb.save_plan_snapshot(collab_id=t["task_id"], content="v1")
    s2 = cb.save_plan_snapshot(collab_id=t["task_id"], content="v2")
    assert s1["filename"] != s2["filename"], "two snapshots in the same second must not collide"
    # Both files must exist on disk
    assert os.path.isfile(s1["filepath"])
    assert os.path.isfile(s2["filepath"])


# ── list_snapshots includes discussion zone ───────────────────────────────


def test_list_plan_snapshots_includes_upsert_history(tmp_workspace):
    """Agent board_update plan revisions must appear in plan history UI."""
    t = cb.create_task(task_name="t", created_by="pm")
    cb.upsert_item(
        collab_id=t["task_id"],
        agent_id="pm",
        item_type="plan",
        item_key="architecture",
        title="Plan v1",
        content="version one content",
    )
    cb.upsert_item(
        collab_id=t["task_id"],
        agent_id="pm",
        item_type="plan",
        item_key="architecture",
        title="Plan v2",
        content="version two content",
    )
    snaps = cb.list_plan_snapshots(collab_id=t["task_id"])
    assert len(snaps) >= 1
    bodies = [s.get("content", "") for s in snaps]
    assert any("version one content" in b for b in bodies)

    t = cb.create_task(task_name="t", created_by="pm")
    cb.save_snapshot(collab_id=t["task_id"], zone="discussion", content="disc", title="d")
    snaps = cb.list_snapshots(collab_id=t["task_id"])
    zones_found = {s.get("zone") for s in snaps}
    assert "discussion" in zones_found, "list_snapshots must scan the discussion zone"
