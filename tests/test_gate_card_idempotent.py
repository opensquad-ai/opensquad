"""One live card per gate, however many callers ask for it.

The field case: three identical 确定需求 cards arrived within thirty-five seconds, each carrying the
requirement text into the group again, because two sessions were racing on the same collaboration and
every call minted a fresh card. Reusing the live card kills the visible duplication whatever the
session count is.

Rejected gates are the exception and stay one: a rejection is an answer, and asking again after
revising is a new question.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad import collab_board as cb  # noqa: E402
from opensquad.tools import collaboration as collab  # noqa: E402

COLLAB = "IDEM01"
GROUP = "g-idem"


def _board(tmp_path, monkeypatch, items):
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    (tmp_path / "board_items.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def _task() -> dict:
    return cb.create_task(task_id=COLLAB, task_name="重拍", created_by="pm", group_id=GROUP)


def _approval(step: str, state: str, key: str, stamp: str) -> dict:
    return {
        "id": f"{COLLAB}:pm:approval:{key}",
        "collab_id": COLLAB,
        "task_id": COLLAB,
        "agent_id": "pm",
        "item_type": "approval",
        "item_key": key,
        "title": step,
        "status": state,
        "visibility": "public",
        "created_at": stamp,
        "updated_at": stamp,
        "extra": {"approval": {"step": step, "status": state}, "kind": "collab_step_approval"},
    }


def _approvals() -> list[dict]:
    return [i for i in cb.list_items(collab_id=COLLAB, visibility="public") if i.get("item_type") == "approval"]


def _raise(step: str) -> dict:
    return collab.request_step_approval(collab_id=COLLAB, step=step, summary="要点", group_id=GROUP)


def test_a_pending_card_is_reused_instead_of_minted_again(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch, [_approval("确定需求", "pending", "appr_live", "2026-10-05T05:03:40")])
    _task()

    result = _raise("确定需求")

    assert result["reused"] is True
    assert result["approval_id"] == "appr_live"
    assert result["status"] == "pending"
    assert len(_approvals()) == 1, "no second card"


def test_an_approved_gate_says_so_so_the_caller_moves_on(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch, [_approval("确定需求", "approved", "appr_ok", "2026-10-05T05:03:40")])
    _task()

    result = _raise("确定需求")

    assert result["status"] == "approved"
    assert result["reused"] is True
    assert "继续下一步" in result["message"]
    assert len(_approvals()) == 1


def test_the_field_case_three_cards_for_one_step(tmp_path, monkeypatch):
    """What 2684CA actually had: three approved 确定需求 cards, twenty-three seconds apart."""
    _board(
        tmp_path,
        monkeypatch,
        [
            _approval("确定需求", "approved", "appr_a", "2026-10-05T05:03:40"),
            _approval("确定需求", "approved", "appr_b", "2026-10-05T05:04:03"),
            _approval("确定需求", "approved", "appr_c", "2026-10-05T05:04:15"),
        ],
    )
    _task()

    result = _raise("确定需求")

    assert result["reused"] is True
    assert result["approval_id"] == "appr_c", "the newest one is the live one"
    assert len(_approvals()) == 3, "asking again adds nothing"


def test_a_rejected_gate_gets_a_new_card(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch, [_approval("讨论方案", "rejected", "appr_no", "2026-10-05T05:04:16")])
    _task()

    result = _raise("讨论方案")

    assert result.get("reused") is not True
    assert result.get("approval_id") != "appr_no"
    assert len(_approvals()) == 2


def test_a_different_step_is_not_answered_with_this_one(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch, [_approval("确定需求", "pending", "appr_live", "2026-10-05T05:03:40")])
    _task()

    result = _raise("讨论方案")

    assert result.get("reused") is not True
    assert len(_approvals()) == 2


def test_a_step_alias_cannot_dodge_the_guard(tmp_path, monkeypatch):
    """`requirements` and 确定需求 are the same gate — normalize_step says so, and so does this."""
    _board(tmp_path, monkeypatch, [_approval("确定需求", "approved", "appr_live", "2026-10-05T05:03:40")])
    _task()

    result = _raise("requirements")

    assert result["reused"] is True
    assert len(_approvals()) == 1


def test_the_group_is_still_required(tmp_path, monkeypatch):
    """The guard sits after group resolution, so a task with no group still reports that instead."""
    _board(tmp_path, monkeypatch, [])
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    cb.create_task(task_id=COLLAB, task_name="无群", created_by="pm")

    result = collab.request_step_approval(collab_id=COLLAB, step="确定需求", summary="x")

    assert "group_id" in str(result.get("message") or ""), result
