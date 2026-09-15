"""Tests for the suggest_followups (对话后续预期) tool.

The tool is non-blocking: it emits one ``info`` bus event carrying a
``suggest_followups`` payload and returns a short acknowledgement. Nothing is
awaited, and there is deliberately no group-card path.
"""

from __future__ import annotations

import asyncio
import json

from opensquad.tools.followup_tools import (
    MAX_SUGGESTIONS,
    _coerce_suggestion_list,
    suggest_followups,
)


class _Bus:
    def __init__(self) -> None:
        self.emitted: list[dict] = []

    async def emit_async(self, channel, payload=None, **kwargs):
        self.emitted.append({"channel": channel, "payload": payload or kwargs})


def _run(monkeypatch, suggestions, **kwargs) -> tuple[str, list[dict]]:
    bus = _Bus()
    monkeypatch.setattr("opensquad.events.bus", bus)
    result = asyncio.run(suggest_followups(suggestions, **kwargs))
    return result, bus.emitted


def test_coerce_plain_string_list():
    assert _coerce_suggestion_list(["甲", "乙"]) == ["甲", "乙"]


def test_coerce_json_string():
    raw = json.dumps(["甲", "乙"], ensure_ascii=False)
    assert _coerce_suggestion_list(raw) == ["甲", "乙"]


def test_coerce_newline_separated_keeps_commas_inside_a_sentence():
    """A follow-up question often contains commas — they must NOT split it."""
    raw = "分析板块与十五五规划的关联，给出证据\n另一半：导出 md 报告"
    assert _coerce_suggestion_list(raw) == [
        "分析板块与十五五规划的关联，给出证据",
        "另一半：导出 md 报告",
    ]


def test_coerce_wrapped_dict_and_nested_string():
    assert _coerce_suggestion_list({"suggestions": ["甲", "乙"]}) == ["甲", "乙"]
    assert _coerce_suggestion_list({"suggestions": '["甲", "乙"]'}) == ["甲", "乙"]


def test_coerce_dict_items_fall_back_to_title_content_label():
    out = _coerce_suggestion_list([{"text": "T"}, {"title": "Ti"}, {"content": "C"}, {"label": "L"}])
    assert out == ["T", "Ti", "C", "L"]


def test_coerce_dedupes_and_drops_junk():
    assert _coerce_suggestion_list(["甲", "甲", "", "  ", None, 42]) == ["甲"]


def test_coerce_single_dict_wrapper():
    assert _coerce_suggestion_list({"text": "只有一条"}) == ["只有一条"]


def test_emits_suggest_followups_payload(monkeypatch):
    result, emitted = _run(monkeypatch, ["导出 md 报告", "分析板块关联"])
    assert len(emitted) == 1
    payload = emitted[0]["payload"]
    assert emitted[0]["channel"] == "info"
    assert payload["event"] == "suggest_followups"
    assert payload["id"].startswith("fu_")
    assert payload["suggestions"] == [
        {"id": "fu_1", "text": "导出 md 报告"},
        {"id": "fu_2", "text": "分析板块关联"},
    ]
    # The acknowledgement must not tell the agent to wait — the offer is
    # non-blocking and the turn ends normally.
    assert "Follow-up suggestions offered" in result
    assert "do NOT wait" in result


def test_accepts_json_string_input(monkeypatch):
    result, emitted = _run(
        monkeypatch,
        json.dumps(["甲", "乙"], ensure_ascii=False),
    )
    assert "Follow-up suggestions offered" in result
    assert [s["text"] for s in emitted[0]["payload"]["suggestions"]] == ["甲", "乙"]


def test_caps_at_max_suggestions(monkeypatch):
    result, emitted = _run(monkeypatch, ["1", "2", "3", "4", "5"])
    assert len(emitted[0]["payload"]["suggestions"]) == MAX_SUGGESTIONS == 3
    assert "Follow-up suggestions offered" in result


def test_text_kwarg_is_a_single_suggestion_fallback(monkeypatch):
    result, emitted = _run(monkeypatch, None, text="兜底的一条")
    assert len(emitted) == 1
    assert emitted[0]["payload"]["suggestions"] == [{"id": "fu_1", "text": "兜底的一条"}]
    assert "Follow-up suggestions offered" in result


def test_empty_input_returns_error_without_emitting(monkeypatch):
    result, emitted = _run(monkeypatch, [])
    assert emitted == []
    assert "1–3 suggestions" in result


def test_non_list_scalar_returns_error(monkeypatch):
    """An int/None-ish payload degrades to a message, never an exception."""
    result, emitted = _run(monkeypatch, 12345)
    assert emitted == []
    assert "1–3 suggestions" in result


def test_persists_to_session_events(monkeypatch):
    bus = _Bus()
    monkeypatch.setattr("opensquad.events.bus", bus)
    saved: list[tuple] = []

    class _SM:
        def add_event(self, kind, payload):
            saved.append((kind, payload))

    monkeypatch.setattr("opensquad.session_manager.session_manager", _SM())
    asyncio.run(suggest_followups(["刷新后也要在"]))
    assert len(saved) == 1
    assert saved[0][0] == "info"
    assert saved[0][1]["event"] == "suggest_followups"


def test_bus_failure_is_reported_not_raised(monkeypatch):
    class _Broken:
        async def emit_async(self, *a, **k):
            raise RuntimeError("bus down")

    monkeypatch.setattr("opensquad.events.bus", _Broken())
    result = asyncio.run(suggest_followups(["甲"]))
    assert "Failed to offer follow-up suggestions" in result
