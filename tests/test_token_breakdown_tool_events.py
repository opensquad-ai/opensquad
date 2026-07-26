"""Tool IO in session events must count toward token breakdown.tool."""

from opensquad.token_breakdown import (
    compute_token_breakdown,
    enrich_req_with_session_tool_events,
    req_has_tool_io,
    synthesize_tool_messages_from_events,
)


def test_synthesize_tool_messages_from_events():
    events = [
        {
            "type": "tool_call",
            "data": {
                "id": "call_1",
                "name": "filesystem__read_file",
                "args": '{"path": "a.py"}',
            },
        },
        {
            "type": "tool_result",
            "data": {
                "id": "call_1",
                "name": "filesystem__read_file",
                "result": "print('hi')\n" * 20,
            },
        },
    ]
    synth = synthesize_tool_messages_from_events(events)
    assert len(synth) == 2
    assert synth[0]["role"] == "assistant"
    assert synth[0]["tool_calls"][0]["function"]["name"] == "filesystem__read_file"
    assert synth[1]["role"] == "tool"
    assert "print" in synth[1]["content"]


def test_enrich_req_appends_when_messages_lack_tool_io():
    msgs = [
        {"role": "user", "content": "read a.py"},
        {"role": "assistant", "content": ""},
    ]
    events = [
        {
            "type": "tool_call",
            "data": {"id": "c1", "name": "read", "args": "{}"},
        },
        {
            "type": "tool_result",
            "data": {"id": "c1", "result": "file body " * 50},
        },
    ]
    assert not req_has_tool_io(msgs)
    enriched = enrich_req_with_session_tool_events(msgs, events)
    assert req_has_tool_io(enriched)
    stats = compute_token_breakdown(enriched, tools=None, total=None)
    assert stats["tool"] > 0
    # Without enrichment, tool stays 0
    bare = compute_token_breakdown(msgs, tools=None, total=None)
    assert bare["tool"] == 0


def test_enrich_skips_when_native_tool_io_present():
    msgs = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    events = [
        {"type": "tool_call", "data": {"id": "c2", "name": "other", "args": "{}"}},
        {"type": "tool_result", "data": {"id": "c2", "result": "extra"}},
    ]
    enriched = enrich_req_with_session_tool_events(msgs, events)
    assert enriched == msgs
