"""A blocked gate must say which one, how long, and what to do — and must never pass itself.

The four gates are the user's approvals, so a blocked phase is not a bug to route around: the
failure this fixes is a phase blocked with nothing to act on — a gate that is `missing` because
nobody ever asked the user, or one that has been `pending` for hours while the PM waits. The refusal
now carries the state and the age of every blocking gate, and says plainly that a gate never opens
by itself.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad import collab_board as cb  # noqa: E402
from opensquad.tools import collaboration as collab  # noqa: E402

COLLAB = "GATE99"


def _ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def _approval(step: str, state: str, minutes_ago: int, key: str = "") -> dict:
    return {
        "id": f"{COLLAB}:pm:approval:{key or step}",
        "collab_id": COLLAB,
        "task_id": COLLAB,
        "item_type": "approval",
        "item_key": key or f"appr_{step}",
        "title": step,
        "status": state,
        "visibility": "public",
        "created_at": _ago(minutes_ago),
        "updated_at": _ago(minutes_ago),
        "extra": {"approval": {"step": step, "state": state}},
    }


def _board(tmp_path, monkeypatch, items):
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    (tmp_path / "board_items.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def test_a_missing_gate_is_reported_as_never_asked(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch, [_approval("确定需求", "pending", 5)])

    report = cb.gate_report(COLLAB)

    assert report["确定需求"]["state"] == "pending"
    assert report["讨论方案"] == {"state": "missing", "since": "", "age_seconds": None, "approval_id": ""}


def test_the_age_of_a_gate_is_reported(tmp_path, monkeypatch):
    _board(
        tmp_path, monkeypatch, [_approval("确定需求", "pending", 90), _approval("讨论方案", "approved", 10, "appr_b")]
    )

    report = cb.gate_report(COLLAB)

    assert 5300 <= report["确定需求"]["age_seconds"] <= 5500, "about ninety minutes, give or take a run"
    assert report["讨论方案"]["state"] == "approved"
    assert report["讨论方案"]["age_seconds"] <= 900


def test_the_newest_item_for_a_step_wins(tmp_path, monkeypatch):
    _board(
        tmp_path,
        monkeypatch,
        [
            _approval("确定需求", "pending", 90, "appr_old"),
            _approval("确定需求", "approved", 5, "appr_new"),
        ],
    )

    assert cb.gate_report(COLLAB)["确定需求"]["state"] == "approved"


def test_the_refusal_names_each_gate_with_its_state_and_age(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch, [_approval("确定需求", "pending", 90)])

    message = collab._gate_requirement_message(COLLAB, ("确定需求", "讨论方案"))

    assert "确定需求：pending（已等待 1 小时）" in message
    assert "讨论方案：从未发起审批" in message
    assert "门不会自动通过" in message, "a stalled gate must say that waiting is not a way through"
    assert "request_step_approval" in message


def test_a_fresh_blocker_does_not_cry_stall(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch, [_approval("确定需求", "pending", 2)])

    message = collab._gate_requirement_message(COLLAB, ("确定需求",))

    assert "已等待 2 分钟" in message
    assert "门不会自动通过" not in message


def test_an_approved_gate_is_not_a_blocker(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch, [_approval("确定需求", "approved", 60)])

    assert collab._gate_requirement_message(COLLAB, ("确定需求",)) == ""


def test_an_old_gate_still_refuses_dispatch(tmp_path, monkeypatch):
    """The safety property: age never approves anything."""
    _board(tmp_path, monkeypatch, [_approval("确定需求", "pending", 600)])
    monkeypatch.setattr(collab, "_not_accepted_message", lambda *a, **k: "not accepted yet")
    monkeypatch.setattr(cb, "get_task", lambda **k: {"extra": {"project_dir": "D:/work/x"}})

    result = collab.assign_task(collab_id=COLLAB, worker_id="pm", task_name="t", item_key="task_x")

    assert result["code"] == "gates_not_approved"
    assert "已等待 10 小时" in result["message"]


def test_the_report_can_be_answered_from_a_paired_machine():
    """It reads the board, so like gate_states it must be dispatched, not read locally."""
    src = (_SRC / "opensquad" / "collab_board.py").read_text(encoding="utf-8")
    ops = src.split("REMOTE_OPS", 1)[1]

    assert '"gate_report"' in ops.split(")", 1)[0]
    assert '"gate_states"' in ops.split(")", 1)[0]
