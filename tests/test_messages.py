"""Unit tests for opensquad.messages -- canonical message model.

Covers the tool-call conversion boundary and full assistant-turn parsing.
"""

from __future__ import annotations

from opensquad.messages import (
    ToolCall,
    ToolResult,
    parse_assistant_turn,
    parse_tool_calls,
)


class TestToolCall:
    def test_tuple_roundtrip(self):
        tc = ToolCall("read_file", {"path": "a.txt"})
        assert tc.to_tuple() == ("read_file", {"path": "a.txt"})
        assert ToolCall.from_tuple(("read_file", {"path": "a.txt"})) == tc

    def test_equality(self):
        assert ToolCall("a", {}) == ToolCall("a", {})
        assert ToolCall("a", {}) != ToolCall("b", {})


class TestToolResult:
    def test_to_event_shape(self):
        tr = ToolResult("c1", "write_file", {"path": "x"}, "ok")
        assert tr.to_event() == {"id": "c1", "name": "write_file", "args": {"path": "x"}, "result": "ok"}


class TestParseToolCalls:
    def test_native_fc_data_wins(self):
        calls = parse_tool_calls("<to_user>ignored</to_user>", [("system.echo", {"text": "hi"})])
        assert calls == [ToolCall("system.echo", {"text": "hi"})]

    def test_xml_fallback(self):
        response = "<tool_call><func>read_file</func><path>a.txt</path></tool_call>"
        calls = parse_tool_calls(response)
        assert calls == [ToolCall("read_file", {"path": "a.txt"})]

    def test_parallel_xml_calls(self):
        response = (
            "<tool_call><func>read_file</func><path>a.txt</path></tool_call>"
            "<tool_call><func>write_file</func><path>b.txt</path><content>hi</content></tool_call>"
        )
        calls = parse_tool_calls(response)
        assert len(calls) == 2
        assert calls[0].name == "read_file"
        assert calls[1].name == "write_file"

    def test_empty_response(self):
        assert parse_tool_calls("") == []
        assert parse_tool_calls("  ") == []

    def test_plain_text_no_tools(self):
        assert parse_tool_calls("<to_user>hello</to_user>") == []


class TestParseAssistantTurn:
    def test_empty(self):
        turn = parse_assistant_turn("")
        assert turn.text == ""
        assert not turn.has_tools
        assert turn.visible_text == ""

    def test_extracts_tags_and_options(self):
        response = (
            "<thought>thinking hard</thought>"
            "<plan>step 1</plan>"
            "<option>Option A</option><option>Option B</option>"
            "<state>working</state>"
            "<to_user>Hello there</to_user>"
        )
        turn = parse_assistant_turn(response)
        assert turn.thought == "thinking hard"
        assert turn.plan == "step 1"
        assert turn.options == ["Option A", "Option B"]
        assert turn.state == "working"
        assert "Hello there" in turn.visible_text
        assert turn.visible_tag == "to_user"
        assert not turn.has_tools

    def test_sleep_and_sys_cmd_tags(self):
        turn = parse_assistant_turn("<sleep>30</sleep><to_system>task_complete</to_system>")
        assert turn.sleep_seconds == "30"
        assert turn.sys_cmd == "task_complete"

    def test_tool_calls_in_turn(self):
        response = "<thought>calling</thought><tool_call><func>ls</func><dir>.</dir></tool_call>"
        turn = parse_assistant_turn(response)
        assert turn.has_tools
        assert turn.tool_calls == [ToolCall("ls", {"dir": "."})]
        assert turn.thought == "calling"

    def test_native_fc_data_in_turn(self):
        turn = parse_assistant_turn("<to_user>done</to_user>", [("system.echo", {"text": "x"})])
        assert turn.tool_calls == [ToolCall("system.echo", {"text": "x"})]
