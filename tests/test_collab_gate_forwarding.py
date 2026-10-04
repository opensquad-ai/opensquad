"""The gate check has to reach the machine that owns the board.

Reported as a blocker: the user approved 确定需求 and 讨论方案, and assign_task still refused with
"确定需求 missing / 讨论方案 missing". The gate check asked gate_states, which read this machine's
own board_items.json directly — and for a collaboration whose board lives on a paired machine, this
machine's file does not contain that task, so every gate read as missing. Gates could therefore
never be satisfied across machines, and no work could be assigned.

The other two readers on that path (pending_members, accepted_members) go through get_task and
list_participants, which are already board operations and were forwarded; gate_states was the only
one reading the file itself. These tests pin it to the same rule.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad import collab_board as cb  # noqa: E402


def test_gate_states_is_a_board_operation():
    assert "gate_states" in cb.REMOTE_OPS


def test_the_tool_asks_with_the_keyword_so_the_hint_resolves():
    tool = (_SRC / "opensquad" / "tools" / "collaboration.py").read_text(encoding="utf-8")

    assert "gate_states(collab_id=collab_id)" in tool
    assert "gate_states(collab_id)" not in tool


def test_the_gate_check_is_forwarded_and_never_reads_the_local_board(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cb, "_board_owners_file", lambda: str(tmp_path / "board_owners.json"))
    monkeypatch.setattr(cb, "board_base_url", lambda **kw: "http://192.168.5.4:9555")
    forwarded: list[str] = []

    def _remote_call(op, args, kwargs):
        forwarded.append(op)
        return {"确定需求": "approved", "讨论方案": "approved"}

    def _local_items():
        raise AssertionError("the gate check read this machine's own board")

    monkeypatch.setattr(cb, "_remote_call", _remote_call)
    monkeypatch.setattr(cb, "_read_items", _local_items)

    states = cb.gate_states(collab_id="B15B90")

    assert forwarded == ["gate_states"]
    assert states["确定需求"] == "approved"


def test_a_local_board_is_still_read_locally(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cb, "board_base_url", lambda **kw: "")

    assert cb.gate_states(collab_id="none-such")["确定需求"] == "missing"
