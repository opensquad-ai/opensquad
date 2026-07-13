"""Tests for the propose_options N-way choice interaction (backend helpers)."""

from __future__ import annotations

import asyncio
import json

from opensquad.collab_approval import (
    encode_propose_options_message,
    parse_approval_payload,
    parse_propose_options_payload,
    patch_propose_options_status_in_content,
)
from opensquad.tools.choice_tools import (
    _normalize_option,
    coerce_options_arg,
    normalize_options_list,
    propose_options,
)


def test_normalize_option_dict():
    opt = _normalize_option({"id": "a", "title": "Browse", "description": "scan dirs"}, 0)
    assert opt == {"id": "a", "title": "Browse", "description": "scan dirs"}


def test_normalize_option_label_value():
    opt = _normalize_option({"label": "网页截图", "value": "screenshot"}, 0)
    assert opt is not None
    assert opt["id"] == "screenshot"
    assert opt["title"] == "网页截图"


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


def test_coerce_options_json_string_list():
    raw = '["网页截图", "搜索测试", "读取图片"]'
    assert coerce_options_arg(raw) == ["网页截图", "搜索测试", "读取图片"]


def test_coerce_options_wrapped_dict_with_label_value():
    raw = {
        "options": [
            {"label": "网页截图", "value": "screenshot"},
            {"label": "搜索测试", "value": "search"},
        ]
    }
    norm = normalize_options_list(raw)
    assert len(norm) == 2
    assert norm[0]["id"] == "screenshot"
    assert norm[0]["title"] == "网页截图"


def test_coerce_options_json_string_of_dicts():
    raw = json.dumps(
        [
            {"id": "screenshot", "title": "网页截图", "description": "打开浏览器截图"},
            {"id": "search", "title": "搜索测试", "description": "搜索关键词"},
        ],
        ensure_ascii=False,
    )
    norm = normalize_options_list(raw)
    assert len(norm) == 2
    assert norm[0]["id"] == "screenshot"


def test_coerce_options_wrapped_json_string():
    raw = json.dumps(
        {"options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}]},
        ensure_ascii=False,
    )
    norm = normalize_options_list(raw)
    assert [o["id"] for o in norm] == ["a", "b"]


def test_propose_options_accepts_json_string_list(monkeypatch):
    emitted: list[dict] = []

    class _Bus:
        async def emit_async(self, channel, payload=None, **kwargs):
            emitted.append({"channel": channel, "payload": payload or kwargs})

    monkeypatch.setattr("opensquad.events.bus", _Bus())
    monkeypatch.setattr(
        "opensquad.collab_approval.resolve_current_group_id",
        lambda _gid="": "",
    )
    result = asyncio.run(
        propose_options(
            "选一个测试",
            '["网页截图", "搜索测试", "读取图片", "转录音频", "文件操作"]',
        )
    )
    assert "Options proposed" in result
    assert len(emitted) == 1
    assert len(emitted[0]["payload"]["options"]) == 5


def test_propose_options_accepts_label_value_wrapper(monkeypatch):
    emitted: list[dict] = []

    class _Bus:
        async def emit_async(self, channel, payload=None, **kwargs):
            emitted.append({"channel": channel, "payload": payload or kwargs})

    monkeypatch.setattr("opensquad.events.bus", _Bus())
    monkeypatch.setattr(
        "opensquad.collab_approval.resolve_current_group_id",
        lambda _gid="": "",
    )
    result = asyncio.run(
        propose_options(
            "选一个测试",
            {
                "options": [
                    {"label": "网页截图", "value": "screenshot"},
                    {"label": "搜索测试", "value": "search"},
                ]
            },
        )
    )
    assert "Options proposed" in result
    assert emitted[0]["payload"]["options"][0]["id"] == "screenshot"


def test_propose_options_allow_multiple_flag(monkeypatch):
    emitted: list[dict] = []

    class _Bus:
        async def emit_async(self, channel, payload=None, **kwargs):
            emitted.append({"channel": channel, "payload": payload or kwargs})

    monkeypatch.setattr("opensquad.events.bus", _Bus())
    monkeypatch.setattr(
        "opensquad.collab_approval.resolve_current_group_id",
        lambda _gid="": "",
    )
    result = asyncio.run(propose_options("多选测试", ["A", "B", "C"], allow_multiple=True))
    assert "multi-select" in result
    assert emitted[0]["payload"]["allow_multiple"] is True


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
    # Plain prose is coerced to a single title → still fails the >=2 check.
    result = asyncio.run(propose_options("pick one", "not a list"))
    assert "at least 2" in result


def test_patch_status_multi_chosen_ids():
    payload = {
        "id": "p1",
        "prompt": "pick",
        "options": [
            {"id": "o1", "title": "A"},
            {"id": "o2", "title": "B"},
            {"id": "o3", "title": "C"},
        ],
        "allow_multiple": True,
        "status": "pending",
    }
    msg = encode_propose_options_message(payload)
    patched = patch_propose_options_status_in_content(msg, "chosen", chosen_ids=["o1", "o3"])
    parsed = parse_propose_options_payload(patched)
    assert parsed is not None
    assert parsed["chosen_option_ids"] == ["o1", "o3"]
    assert parsed["chosen_option_id"] == "o1"


def test_propose_options_group_skips_agent_web_bus_event(monkeypatch):
    """When a group card posts successfully, do not also emit Agent Web bus event."""
    emitted: list[tuple] = []

    class _Bus:
        async def emit_async(self, *args, **kwargs):
            emitted.append((args, kwargs))

    monkeypatch.setattr("opensquad.events.bus", _Bus())
    monkeypatch.setattr(
        "opensquad.collab_approval.resolve_current_group_id",
        lambda _gid="": "g1",
    )
    monkeypatch.setattr(
        "opensquad.collab_approval.resolve_agent_identity",
        lambda: ("agent305", "Agent305"),
    )
    monkeypatch.setattr(
        "opensquad.collab_approval.post_group_propose_options_card",
        lambda payload, gid: {"ok": True, "group_id": gid, "message_id": "m1"},
    )

    result = asyncio.run(
        propose_options(
            "选一个功能测试：",
            [
                {"title": "搜索", "description": "搜新闻"},
                {"title": "文件", "description": "列目录"},
            ],
        )
    )
    assert emitted == []
    assert "group" in result.lower()
    assert "Agent Web" in result or "group chat" in result.lower()


def test_propose_options_private_emits_bus_when_no_group(monkeypatch):
    emitted: list[dict] = []

    class _Bus:
        async def emit_async(self, channel, payload=None, **kwargs):
            emitted.append({"channel": channel, "payload": payload or kwargs})

    monkeypatch.setattr("opensquad.events.bus", _Bus())
    monkeypatch.setattr(
        "opensquad.collab_approval.resolve_current_group_id",
        lambda _gid="": "",
    )

    result = asyncio.run(propose_options("pick", ["A plan", "B plan"]))
    assert len(emitted) == 1
    assert emitted[0]["payload"]["event"] == "propose_options"
    assert "Agent Web" in result or "chat UI" in result


def test_coerce_command_data_prefers_nested_data():
    """Group/API resolve must put fields under data — adapter reads that path."""
    from opensquad.gateway_adapter import coerce_command_data

    msg = {
        "type": "command",
        "command": "resolve_proposed_options",
        "user_id": "u1",
        "data": {
            "id": "opt_abc",
            "chosen_option_id": "screenshot",
            "chosen_option_ids": ["screenshot"],
            "ignored": False,
        },
    }
    out = coerce_command_data(msg)
    assert out["id"] == "opt_abc"
    assert out["chosen_option_id"] == "screenshot"
    assert out["chosen_option_ids"] == ["screenshot"]


def test_coerce_command_data_recovers_top_level_fields():
    """Regression: old group resolve put id at top level; adapter must still wake."""
    from opensquad.gateway_adapter import coerce_command_data

    broken = {
        "type": "command",
        "command": "resolve_proposed_options",
        "id": "opt_abc",
        "chosen_option_id": "search",
        "chosen_option_ids": ["search"],
        "custom_answer": "",
        "ignored": False,
    }
    out = coerce_command_data(broken)
    assert out["id"] == "opt_abc"
    assert out["chosen_option_id"] == "search"
    assert out["ignored"] is False
