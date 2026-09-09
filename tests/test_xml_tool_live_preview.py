"""Live XML tool-call preview: unclosed tags must surface a tool row, not leak as chat."""

from __future__ import annotations

from opensquad.parser import ResponseParser
from opensquad.xml_parser import StreamingTagParser
from opensquad.xml_tool_preview import attach_xml_tool_preview


def test_peek_unclosed_tool_call_emits_delta_and_does_not_leak():
    leaked: list[str] = []
    previews: list[tuple[str, dict]] = []
    parser = StreamingTagParser(
        handlers={"tool_call": lambda _x: None, "thought": lambda _x: None},
        default_handler=lambda x: leaked.append(x),
    )
    attach_xml_tool_preview(parser, lambda et, data: previews.append((et, data)))

    parser.feed("<thought>will search</thought>")
    parser.feed("<tool_call>\n<func>websearch.search</func>\n<query>福州天气")

    peeked = parser.peek_commit_state()
    assert peeked is not None
    assert peeked[0] == "tool_call"
    assert "websearch.search" in peeked[1]

    deltas = [p for p in previews if p[0] == "tool_call_delta"]
    assert deltas, "unclosed <tool_call> must emit tool_call_delta before the closing tag"
    payload = deltas[-1][1]
    assert "websearch" in str(payload.get("name") or "")
    assert "福州" in str(payload.get("arguments") or payload.get("args") or "")

    parser.finish()
    assert not any("<tool_call" in x for x in leaked)
    unclosed = parser.take_unclosed_commit()
    assert unclosed is not None
    assert "websearch.search" in unclosed["buffer"]


def test_pretty_printed_open_tag_survives_newline():
    leaked: list[str] = []
    parser = StreamingTagParser(
        handlers={"tool_call": lambda _x: None},
        default_handler=lambda x: leaked.append(x),
    )
    parser.feed('<tool_call\n name="websearch.search">\n<query>福州天气')
    peeked = parser.peek_commit_state()
    assert peeked is not None, f"newline in open tag leaked instead: {leaked!r}"
    assert peeked[2].get("name") == "websearch.search"


def test_parse_unclosed_func_query_executes():
    text = "<tool_call>\n<func>websearch.search</func>\n<query>福州天气"
    result = ResponseParser.parse_tool_calls(text)
    assert result, "unclosed XML tool call with query must parse for execution"
    name, args = result[0]
    assert "websearch" in name
    assert "福州" in str(args.get("query") or args)


def test_parse_unclosed_attr_style():
    text = '<tool_call name="websearch.search">\n<arguments>{"query": "福州天气"}'
    result = ResponseParser.parse_tool_calls(text)
    assert result
    name, args = result[0]
    assert "websearch" in name
    assert "福州" in str(args.get("query") or args)


def test_parse_partial_preview_name_only():
    parsed = ResponseParser.parse_partial_tool_preview("tool_call", "<func>websearch.search", {})
    assert parsed is not None
    assert parsed[0] == "websearch.search"


def test_thought_mention_of_tool_call_is_not_executed():
    text = "<thought>I will use a <tool_call> tag later</thought>Just chatting."
    result = ResponseParser.parse_tool_calls(text)
    assert result == []


def test_ling_first_line_name_arg_key_value_executes():
    text = "<tool_call>websearch\n<arg_key>query</arg_key>\n<arg_value>福州天气</arg_value>\n</tool_call>"
    result = ResponseParser.parse_tool_calls(text)
    assert result, "Ling/Qwen first-line name + arg_key/arg_value must parse"
    name, args = result[0]
    assert name == "websearch"
    assert "福州" in str(args.get("query") or "")


def test_ling_unclosed_arg_key_value_preview_and_execute():
    text = "<tool_call>websearch\n<arg_key>query</arg_key>\n<arg_value>福州天气"
    peeked = ResponseParser.parse_partial_tool_preview("tool_call", text.split(">", 1)[1], {})
    assert peeked is not None
    assert peeked[0] == "websearch"
    assert "福州" in str(peeked[1].get("query") or "")
    result = ResponseParser.parse_tool_calls(text)
    assert result
    assert result[0][0] == "websearch"


def test_ling_peek_emits_delta_for_arg_key_format():
    leaked: list[str] = []
    previews: list[tuple[str, dict]] = []
    parser = StreamingTagParser(
        handlers={"tool_call": lambda _x: None, "thought": lambda _x: None},
        default_handler=lambda x: leaked.append(x),
    )
    attach_xml_tool_preview(parser, lambda et, data: previews.append((et, data)))
    parser.feed("<tool_call>websearch\n<arg_key>query</arg_key>\n<arg_value>福州天气")
    deltas = [p for p in previews if p[0] == "tool_call_delta"]
    assert deltas, "Ling arg_key format must emit live tool_call_delta"
    assert "websearch" in str(deltas[-1][1].get("name") or "")
    parser.finish()
    assert not any("<tool_call" in x for x in leaked)
