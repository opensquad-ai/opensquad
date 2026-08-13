"""Compact OpenAI tool schema used on the Native-FC hot path."""

from opensquad.registry import (
    _FN_DESC_MAX,
    _FN_DESC_MAX_EXTENDED,
    _PARAM_DESC_MAX,
    _clip_desc,
    _compact_openai_tool,
)


def test_clip_desc_keeps_short_text():
    assert _clip_desc("hello", 10) == "hello"


def test_clip_desc_truncates_with_ellipsis():
    out = _clip_desc("x" * 40, 10)
    assert len(out) == 10
    assert out.endswith("…")


def test_compact_core_keeps_clipped_param_docs():
    long_fn = "F" * (_FN_DESC_MAX + 40)
    long_param = "P" * (_PARAM_DESC_MAX + 40)
    tool = {
        "type": "function",
        "function": {
            "name": "filesystem__read_file",
            "description": long_fn,
            "parameters": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string", "description": long_param},
                },
            },
        },
    }
    out = _compact_openai_tool(tool, strip_param_desc=False, fn_desc_max=_FN_DESC_MAX)
    fn = out["function"]
    assert fn["name"] == "filesystem__read_file"
    assert len(fn["description"]) <= _FN_DESC_MAX
    path_schema = fn["parameters"]["properties"]["path"]
    assert path_schema["type"] == "string"
    assert "description" in path_schema
    assert len(path_schema["description"]) <= _PARAM_DESC_MAX
    assert fn["parameters"]["required"] == ["path"]


def test_compact_extended_strips_param_descriptions():
    tool = {
        "type": "function",
        "function": {
            "name": "websearch__search",
            "description": "Search the public web for recent results and citations " * 8,
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                },
            },
        },
    }
    out = _compact_openai_tool(tool, strip_param_desc=True, fn_desc_max=_FN_DESC_MAX_EXTENDED)
    fn = out["function"]
    assert fn["name"] == "websearch__search"
    assert len(fn["description"]) <= _FN_DESC_MAX_EXTENDED
    assert "description" not in fn["parameters"]["properties"]["query"]
    assert fn["parameters"]["required"] == ["query"]
