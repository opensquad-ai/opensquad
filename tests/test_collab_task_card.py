"""Collaboration-task cards ([[COLLAB_TASK]]) — protocol + board metadata."""

from __future__ import annotations

import pytest

from opensquad import collab_approval as ca
from opensquad import collab_board as cb


def _card_content(**overrides) -> str:
    payload = ca.build_collab_task_payload(
        collab_id=overrides.pop("collab_id", "AB12CD"),
        title=overrides.pop("title", "自建应用发布"),
        **overrides,
    )
    return ca.encode_collab_task_message(payload)


# --------------------------------------------------------------------------
# Protocol
# --------------------------------------------------------------------------
def test_build_parse_round_trip():
    content = _card_content(
        kind="invite",
        card="software_dev_team",
        group_id="g-default",
        summary="做一个审核流程",
        participants=[
            {"agent_id": "coder-001", "name": "coder-001", "state": "accepted"},
            {"agent_id": "qa", "name": "QA", "state": "invited"},
        ],
    )
    payload = ca.parse_collab_task_payload(content)
    assert payload is not None
    assert payload["collab_id"] == "AB12CD"
    assert payload["kind"] == "invite"
    assert payload["card"] == "software_dev_team"
    assert payload["title"] == "自建应用发布"
    states = {p["agent_id"]: p["state"] for p in payload["participants"]}
    assert states == {"coder-001": "accepted", "qa": "invited"}


def test_an_assignment_does_not_post_a_card_to_the_group():
    """Assignments are board items: a card per assignment filled the group with
    '打开任务窗口' bubbles. The group keeps the @mention that wakes the worker."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "opensquad" / "tools" / "collaboration.py").read_text(
        encoding="utf-8"
    )

    assert 'kind="assign"' not in source
    # the notification itself stays (the worker must be woken and told where to look)
    assert "[Task Assigned]" in source


def test_kind_and_state_normalization():
    assert ca.normalize_task_kind("END") == "done"
    assert ca.normalize_task_kind("weird") == "discussion"
    assert ca.normalize_participant_state("joined") == "accepted"
    assert ca.normalize_participant_state("refused") == "declined"
    assert ca.normalize_participant_state("") == "invited"


def test_parse_requires_collab_id_and_id():
    assert ca.parse_collab_task_payload("") is None
    assert ca.parse_collab_task_payload("plain text") is None
    # marker present but body malformed / missing collab_id
    bad = f"{ca.COLLAB_TASK_START}{{}}{ca.COLLAB_TASK_END}"
    assert ca.parse_collab_task_payload(bad) is None
    no_cid = f'{ca.COLLAB_TASK_START}{{"id":"ctask_x"}}{ca.COLLAB_TASK_END}'
    assert ca.parse_collab_task_payload(no_cid) is None


def test_markers_do_not_collide():
    task_content = _card_content()
    approval_content = ca.encode_approval_message(
        ca.build_approval_payload(approval_id="appr_1", title="t", kind="collab_step", collab_id="AB12CD")
    )
    # each parser ignores the other's marker
    assert ca.parse_approval_payload(task_content) is None
    assert ca.parse_collab_task_payload(approval_content) is None
    # and stripping only removes its own marker
    assert ca.COLLAB_TASK_START not in ca.strip_collab_task_marker(task_content)
    assert ca.strip_collab_task_marker(approval_content) == approval_content
    assert (
        ca.GROUP_APPROVAL_START in ca.strip_collab_task_marker(approval_content)
        or "COLLAB_APPROVAL" in approval_content
    )


def test_encode_keeps_agent_readable_fallback():
    content = _card_content(kind="invite", card="code_review", participants=[{"agent_id": "qa", "state": "invited"}])
    assert "Task ID: AB12CD" in content
    assert "Collab Card: code_review" in content
    assert 'join_collaboration(card="code_review", collab_id="AB12CD")' in content
    assert "qa(已邀请)" in content


def test_patch_status_is_idempotent():
    content = _card_content(kind="progress")
    once = ca.patch_collab_task_status_in_content(content, "done", note="ok")
    twice = ca.patch_collab_task_status_in_content(once, "done", note="ok")
    assert once == twice
    payload = ca.parse_collab_task_payload(twice)
    assert payload["status"] == "done"
    assert payload["resolve_note"] == "ok"
    # readable text survives the rewrite
    assert "Task ID: AB12CD" in twice


def test_patch_participant_updates_and_appends():
    content = _card_content(participants=[{"agent_id": "qa", "name": "QA", "state": "invited"}])
    updated = ca.patch_collab_task_participant_in_content(content, "qa", "accepted")
    payload = ca.parse_collab_task_payload(updated)
    assert payload["participants"][0]["state"] == "accepted"
    # unknown participant is appended rather than dropped
    updated2 = ca.patch_collab_task_participant_in_content(updated, "coder-001", "declined")
    states = {p["agent_id"]: p["state"] for p in ca.parse_collab_task_payload(updated2)["participants"]}
    assert states == {"qa": "accepted", "coder-001": "declined"}
    # a non-card body is returned untouched
    assert ca.patch_collab_task_participant_in_content("nothing", "qa", "accepted") == "nothing"


# --------------------------------------------------------------------------
# Board metadata + summary
# --------------------------------------------------------------------------
@pytest.fixture()
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    return cb


def test_task_extra_card_skills_participants(board):
    rec = board.create_task(task_name="发布流程", created_by="pm")
    cid = rec["task_id"]

    board.set_card_and_skills(collab_id=cid, card="software_dev_team", skills=["python", "git"])
    board.set_card_and_skills(collab_id=cid, skills=["python", "docker"], files=["src/a.py"])
    task = board.get_task(task_id=cid)
    assert task["extra"]["card"] == "software_dev_team"
    assert task["extra"]["skills"] == ["python", "git", "docker"]
    assert task["extra"]["files"] == ["src/a.py"]

    board.mark_participant(collab_id=cid, agent_id="qa", state="invited", name="QA")
    board.mark_participant(collab_id=cid, agent_id="qa", state="accepted")
    board.mark_participant(collab_id=cid, agent_id="coder", state="invited")
    parts = board.list_participants(collab_id=cid)
    by_id = {p["agent_id"]: p for p in parts}
    assert by_id["qa"]["state"] == "accepted"
    assert by_id["qa"]["name"] == "QA"
    assert by_id["qa"]["responded_at"]
    assert by_id["coder"]["state"] == "invited"
    # invited rows come first, accepted last
    assert [p["agent_id"] for p in parts] == ["coder", "qa"]


def test_mark_participant_normalizes_state(board):
    cid = board.create_task(task_name="t", created_by="pm")["task_id"]
    board.mark_participant(collab_id=cid, agent_id="x", state="weird")
    assert board.list_participants(collab_id=cid)[0]["state"] == "invited"


def test_board_helpers_tolerate_unknown_task(board):
    assert board.get_task(task_id="NOPE") is None
    assert board.set_card_and_skills(collab_id="NOPE", card="c") == {}
    assert board.mark_participant(collab_id="NOPE", agent_id="a", state="invited") == {}
    assert board.list_participants(collab_id="NOPE") == []


def test_board_summary_groups_items(board):
    cid = board.create_task(task_name="发布流程", created_by="pm")["task_id"]
    board.set_card_and_skills(collab_id=cid, card="software_dev_team", skills=["python"])
    board.mark_participant(collab_id=cid, agent_id="qa", state="invited")
    board.upsert_item(collab_id=cid, agent_id="pm", item_type="requirement", title="需求", content="要做审核")
    board.upsert_item(collab_id=cid, agent_id="pm", item_type="plan", title="方案", content="分三步")
    board.upsert_item(
        collab_id=cid,
        agent_id="coder",
        item_type="task",
        title="实现",
        content="[ ] 1.1 实现",
        item_key="task_impl",
        extra={"file_scope": "src/a.py,src/b.py"},
    )
    board.append_public_discussion(
        collab_id=cid, task_name="发布流程", author_agent_id="coder", title="讨论", content="我开始做了"
    )
    # the group the task runs in, so the window can resolve its approval gates
    board.update_task(task_id=cid, extra={"group_id": "g-default"})
    board.upsert_item(
        collab_id=cid,
        agent_id="pm",
        item_type="approval",
        item_key="appr_1",
        title="确定需求",
        content="需求已确认",
        status="pending",
        extra={
            "approval": {"step": "确定需求", "collab_id": cid, "agent_name": "pm"},
            "kind": "collab_step_approval",
            "message_id": "m_1",
        },
    )

    summary = board.board_summary(collab_id=cid)
    assert summary["collab_id"] == cid
    assert summary["title"] == "发布流程"
    assert summary["card"] == "software_dev_team"
    assert summary["skills"] == ["python"]
    assert summary["task"]["extra"]["group_id"] == "g-default"
    assert {p["agent_id"] for p in summary["participants"]} == {"qa"}
    assert len(summary["items"]["requirement"]) == 1
    assert len(summary["items"]["plan"]) == 1
    assert len(summary["items"]["task"]) == 1
    assert len(summary["items"]["discussion"]) == 1
    assert "src/a.py" in summary["files"] and "src/b.py" in summary["files"]
    # approval gates reach the window with everything it needs to render and
    # resolve them (step label, status, and the group message id)
    assert len(summary["items"]["approval"]) == 1
    gate = summary["items"]["approval"][0]
    assert gate["status"] == "pending"
    assert gate["extra"]["approval"]["step"] == "确定需求"
    assert gate["extra"]["message_id"] == "m_1"


def test_board_summary_requires_collab_id(board):
    with pytest.raises(ValueError):
        board.board_summary(collab_id="")
