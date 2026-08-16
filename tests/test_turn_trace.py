"""Turn identity, cancelled-history sealing, and last-N summaries."""

from __future__ import annotations

import json
from pathlib import Path

from opensquad.gateway_adapter import GatewayAdapter
from opensquad.turn_trace import (
    CANCELLED_TOOL_RESULT,
    append_turn_summary_file,
    has_unclosed_tool_call,
    make_trace_id,
    seal_cancelled_history,
)


class _FakeChatAPI:
    def __init__(self, req: list):
        self.req = req
        self.added: list[str] = []

    def pop_last_assistant_message(self) -> bool:
        if self.req and self.req[-1].get("role") == "assistant":
            self.req.pop()
            return True
        return False

    def add_tool_result(self, tool_name, tool_args, result, tool_call_id=""):
        self.added.append(tool_call_id)
        self.req.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "content": result,
            }
        )


def test_make_trace_id_stable():
    assert make_trace_id("agent305", "sid-a", 12, 3) == "agent305:sid-a:12:3"
    assert make_trace_id(None, None, None, None) == "-:-:0:0"


def test_has_unclosed_tool_call():
    assert has_unclosed_tool_call("<tool_call><name>x</name>") is True
    assert has_unclosed_tool_call("<tool_call>x</tool_call>") is False
    assert has_unclosed_tool_call("hello") is False


def test_seal_pops_unclosed_xml_tool_call():
    api = _FakeChatAPI(
        [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": "<tool_call>\n<name>shell</name>\n"},
        ]
    )
    sealed = seal_cancelled_history(api)
    assert sealed["open_tool_ids"] == []
    assert sealed["partial_persisted"] is False
    assert api.req[-1]["role"] == "user"


def test_seal_appends_cancelled_native_fc_results():
    api = _FakeChatAPI(
        [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "edit"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "write", "arguments": "{}"},
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "edit", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]
    )
    sealed = seal_cancelled_history(api)
    assert sealed["open_tool_ids"] == ["call_2"]
    assert api.added == ["call_2"]
    assert api.req[-1]["role"] == "tool"
    assert api.req[-1]["tool_call_id"] == "call_2"
    assert CANCELLED_TOOL_RESULT in api.req[-1]["content"]
    ids = [m.get("tool_call_id") for m in api.req if m.get("role") == "tool"]
    assert "call_1" in ids and "call_2" in ids


def test_append_turn_summary_file_caps_at_50(tmp_path: Path):
    agent_dir = tmp_path / "agent"
    for i in range(52):
        append_turn_summary_file(str(agent_dir), {"i": i, "trace_id": f"t{i}"})
    path = agent_dir / "data" / "ai_his_talk" / "turn_summaries.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert len(rows) == 50
    assert rows[0]["i"] == 2
    assert rows[-1]["i"] == 51


def test_extract_turn_meta_from_emit_wrapper():
    wrapper = {
        "sid": "s1",
        "data": {"reason": "user_stop"},
        "turn_id": 2,
        "round_id": 9,
        "agent_id": "agent305",
        "trace_id": "agent305:s1:9:2",
    }
    meta = GatewayAdapter._extract_turn_meta(wrapper)
    assert meta["turn_id"] == 2
    assert meta["round_id"] == 9
    assert meta["agent_id"] == "agent305"
    assert meta["trace_id"] == "agent305:s1:9:2"
    assert GatewayAdapter._unwrap(wrapper) == {"reason": "user_stop"}
    assert GatewayAdapter._extract_sid(wrapper) == "s1"
