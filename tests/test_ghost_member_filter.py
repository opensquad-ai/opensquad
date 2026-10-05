"""The ghost-id filter must read the id out of the record, not test the record.

``pending_members`` returns ``{"agent_id", "state", "name"}`` records. A filter that ran the regex
against the whole dict matched nothing, classified every real member as "cannot be an agent name",
emptied the pending list and switched the "nobody gets work before accepting" check off — while
logging that it was ignoring impossible ids. That is how a beta nearly shipped with the enforcement
disabled.
"""

import re
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "tools" / "collaboration.py"
BOARD = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "collab_board.py"


def test_pending_members_returns_records_not_ids():
    """The premise the filter depends on — pinned here so it cannot drift silently."""
    text = BOARD.read_text(encoding="utf-8")
    block = text[text.index("def pending_members") : text.index("def accepted_members")]
    assert 'out.append({"agent_id": agent_id' in block
    assert "return out" in block


def test_the_filter_takes_the_id_from_the_record():
    text = TOOL.read_text(encoding="utf-8")
    block = text[text.index("def _member_id(") : text.index("if pending:")]

    assert "def _member_id(" in block, "the id must be extracted, not the record tested"
    assert "_usable_member(_member_id(m))" in block
    assert "_usable_member(m)" not in block, "testing the record matches nothing"


def test_the_enforcement_still_refuses_when_a_member_has_not_accepted():
    """Behaviour, not source: an invited member blocks assignment, and the error is actionable."""
    import os
    import sys as _sys
    import tempfile

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from opensquad.system_config import syscfg

    tmp = tempfile.mkdtemp(prefix="ghost-filter-")
    original = syscfg.workspace_data_dir
    syscfg.workspace_data_dir = lambda *parts: os.path.join(tmp, "_".join(str(p) for p in parts))
    try:
        import opensquad.collab_board as cb
        from opensquad.tools import collaboration as collab_tool

        tid = cb.create_task(task_name="t", created_by="pm", group_id="g")["task_id"]
        for step in ("确定需求", "讨论方案"):
            cb.upsert_item(
                collab_id=tid,
                task_name="t",
                agent_id="pm",
                item_type="approval",
                item_key=f"ap_{step}",
                title=step,
                content=step,
                status="approved",
                extra={"approval": {"step": step}},
            )
        cb.set_card_and_skills(collab_id=tid, project_dir="D:/work/snake")
        cb.mark_participant(collab_id=tid, agent_id="coder", state="invited")
        cb.mark_participant(collab_id=tid, agent_id="qa", state="accepted")

        res = collab_tool.assign_task(collab_id=tid, worker_id="qa", task_name="写游戏")
    finally:
        syscfg.workspace_data_dir = original

    assert res["status"] == "error", res
    assert res["code"] == "members_not_accepted", res
    assert "coder" in res["message"]


def test_a_malformed_member_is_still_stepped_over(tmp_path):
    """The original report stays fixed: ids that cannot be agent names never block work."""
    import os
    import sys as _sys
    import tempfile

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from opensquad.system_config import syscfg

    tmp = tempfile.mkdtemp(prefix="ghost-ok-")
    original = syscfg.workspace_data_dir
    syscfg.workspace_data_dir = lambda *parts: os.path.join(tmp, "_".join(str(p) for p in parts))
    try:
        import opensquad.collab_board as cb
        from opensquad.tools import collaboration as collab_tool

        tid = cb.create_task(task_name="t", created_by="pm", group_id="g")["task_id"]
        for step in ("确定需求", "讨论方案"):
            cb.upsert_item(
                collab_id=tid,
                task_name="t",
                agent_id="pm",
                item_type="approval",
                item_key=f"ap_{step}",
                title=step,
                content=step,
                status="approved",
                extra={"approval": {"step": step}},
            )
        cb.set_card_and_skills(collab_id=tid, project_dir="D:/work/snake")
        # The mangled members list from the field report: '["pm"]' spread character by character.
        for ghost in ("[", '"', "]", "p", "m"):
            cb.mark_participant(collab_id=tid, agent_id=ghost, state="invited")
        cb.mark_participant(collab_id=tid, agent_id="qa", state="accepted")

        res = collab_tool.assign_task(collab_id=tid, worker_id="qa", task_name="写游戏")
    finally:
        syscfg.workspace_data_dir = original

    assert res["status"] == "success", res
    assert re.search(r"[A-Za-z]", "qa")
