"""Tests for the propose_options N-way choice interaction (backend helpers)."""

from __future__ import annotations

import asyncio

from opensquad.collab_approval import (
    encode_propose_options_message,
    parse_approval_payload,
    parse_propose_options_payload,
    patch_propose_options_status_in_content,
)
from opensquad.tools.choice_tools import _normalize_option, propose_options


def test_normalize_option_dict():
    opt = _normalize_option({"id": "a", "title": "Browse", "description": "scan dirs"}, 0)
    assert opt == {"id": "a", "title": "Browse", "description": "scan dirs"}


def test_normalize_option_string_auto_id():
    opt = _normalize_option("Code search", 1)
    assert opt is not None
    assert opt["id"] == "opt_2"
    assert opt["title"] == "Code search"
    assert opt["description"] == ""


def test_normalize_option_rejects_empty():
    assert _normalize_option("", 0) is None
    assert _normalize_option({"title": ""}, 0) is None
    assert _normalize_option(123, 0) is None  # type: ignore[arg-type]


def test_encode_parse_roundtrip():
    payload = {
        "id": "p1",
        "prompt": "请选择一个任务",
        "options": [
            {"id": "o1", "title": "浏览项目结构", "description": "扫描目录"},
            {"id": "o2", "title": "代码搜索", "description": "搜索关键词"},
        ],
        "allow_custom": True,
        "status": "pending",
    }
    msg = encode_propose_options_message(payload)
    assert "[[PROPOSE_OPTIONS]]" in msg
    parsed = parse_propose_options_payload(msg)
    assert parsed is not None
    assert parsed["id"] == "p1"
    assert len(parsed["options"]) == 2
    assert parsed["options"][0]["title"] == "浏览项目结构"


def test_parse_propose_options_distinct_from_approval():
    """The PROPOSE_OPTIONS marker must NOT be picked up by the approval parser."""
    payload = {
        "id": "p1",
        "prompt": "pick",
        "options": [{"id": "o1", "title": "A"}, {"id": "o2", "title": "B"}],
        "status": "pending",
    }
    msg = encode_propose_options_message(payload)
    assert parse_approval_payload(msg) is None
    assert parse_propose_options_payload(msg) is not None


def test_parse_propose_options_rejects_approval_marker():
    """An approve/reject card must NOT be picked up by the propose-options parser."""
    approval_msg = (
        "[[GROUP_APPROVAL]]"
        '{"id":"a1","kind":"generic","title":"t","status":"pending"}'
        "[[/GROUP_APPROVAL]]\n✋ 批准请求：t"
    )
    assert parse_propose_options_payload(approval_msg) is None


def test_parse_propose_options_rejects_missing_options():
    msg = '[[PROPOSE_OPTIONS]]{"id":"p1","prompt":"x","status":"pending"}[[/PROPOSE_OPTIONS]]'
    assert parse_propose_options_payload(msg) is None


def test_patch_status_choose():
    payload = {
        "id": "p1",
        "prompt": "pick",
        "options": [{"id": "o1", "title": "A"}, {"id": "o2", "title": "B"}],
        "status": "pending",
    }
    msg = encode_propose_options_message(payload)
    patched = patch_propose_options_status_in_content(msg, "chosen", chosen="o2")
    parsed = parse_propose_options_payload(patched)
    assert parsed is not None
    assert parsed["status"] == "chosen"
    assert parsed["chosen_option_id"] == "o2"


def test_patch_status_custom_stores_answer():
    payload = {
        "id": "p1",
        "prompt": "pick",
        "options": [{"id": "o1", "title": "A"}, {"id": "o2", "title": "B"}],
        "status": "pending",
    }
    msg = encode_propose_options_message(payload)
    patched = patch_propose_options_status_in_content(msg, "custom", custom="do something else", note="ok")
    parsed = parse_propose_options_payload(patched)
    assert parsed is not None
    assert parsed["status"] == "custom"
    assert parsed["custom_answer"] == "do something else"
    assert parsed["resolve_note"] == "ok"


def test_patch_status_noop_on_non_matching_content():
    out = patch_propose_options_status_in_content("plain text no marker", "chosen", chosen="o1")
    assert out == "plain text no marker"


def test_propose_options_returns_error_string_for_one_option():
    """With <2 valid options, propose_options returns an error string (no event emitted)."""
    result = asyncio.run(propose_options("pick one", ["only one option"]))
    assert "at least 2" in result


def test_propose_options_returns_error_for_non_list_options():
    result = asyncio.run(propose_options("pick one", "not a list"))  # type: ignore[arg-type]
    assert "Invalid options" in result
