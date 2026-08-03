"""Unit tests for _runner/_compression.py -- summary payload building.

Covers: token estimation, file-operation tracking, budget-based truncation,
and the backward-compatible build_summary_payload signature.
"""

from __future__ import annotations

from opensquad._runner._compression import (
    build_summary_payload,
    estimate_context_tokens,
    estimate_tokens,
    extract_file_operations,
)

# ── estimate_tokens ───────────────────────────────────────────────────────


class TestEstimateTokens:
    def test_empty_text(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens(None) == 0

    def test_short_text_positive(self):
        assert estimate_tokens("hello world") > 0

    def test_heuristic_fallback(self):
        # 1 token ≈ 1.3 chars mixed CJK/English; heuristic is len//2
        text = "a" * 100
        assert estimate_tokens(text) >= 1


# ── estimate_context_tokens ───────────────────────────────────────────────


class TestEstimateContextTokens:
    def test_empty(self):
        stats = estimate_context_tokens([], [])
        assert stats["total"] == 0
        assert stats["message_count"] == 0
        assert stats["event_count"] == 0

    def test_breakdown(self):
        messages = [{"role": "user", "content": "hello"}]
        events = [{"type": "tool_call", "data": {"name": "read_file", "args": '{"path": "a.txt"}'}}]
        stats = estimate_context_tokens(messages, events)
        assert stats["message_count"] == 1
        assert stats["event_count"] == 1
        assert stats["total"] == stats["messages"] + stats["events"]
        assert stats["total"] > 0


# ── extract_file_operations ───────────────────────────────────────────────


def _tool_event(name: str, args: dict | str) -> dict:
    return {"type": "tool_call", "data": {"name": name, "args": args}}


class TestExtractFileOperations:
    def test_read_and_modify(self):
        events = [
            _tool_event("read_file", {"path": "src/a.py"}),
            _tool_event("write_file", {"path": "src/b.py", "content": "x"}),
            _tool_event("replace_in_file", {"file_path": "src/a.py"}),
        ]
        ops = extract_file_operations([], events)
        assert ops["modified"] == ["src/a.py", "src/b.py"]
        assert ops["read"] == ["src/a.py"]

    def test_string_args_json(self):
        events = [_tool_event("read_file", '{"path": "doc.md"}')]
        ops = extract_file_operations([], events)
        assert ops["read"] == ["doc.md"]

    def test_mcp_prefixed_tool(self):
        events = [_tool_event("mcp__fs__write_file", {"path": "out.txt"})]
        ops = extract_file_operations([], events)
        assert ops["modified"] == ["out.txt"]

    def test_case_insensitive_dedup(self):
        events = [_tool_event("read_file", {"path": "A.TXT"}), _tool_event("read_file", {"path": "a.txt"})]
        ops = extract_file_operations([], events)
        assert ops["read"] == ["A.TXT"]

    def test_non_file_tool_ignored(self):
        events = [_tool_event("send_message", {"text": "hi"})]
        ops = extract_file_operations([], events)
        assert ops["read"] == []
        assert ops["modified"] == []

    def test_changeset_files_merged_as_modified(self):
        events = [_tool_event("read_file", {"path": "keep.txt"})]
        changeset = [{"path": "gen/config.json", "status": "M"}, {"path": "keep.txt", "status": "A"}]
        ops = extract_file_operations([], events, changeset_files=changeset)
        assert "keep.txt" in ops["modified"]
        assert "gen/config.json" in ops["modified"]
        assert "keep.txt" in ops["read"]

    def test_sorted_deterministic(self):
        events = [_tool_event("write_file", {"path": "z.txt"}), _tool_event("write_file", {"path": "a.txt"})]
        ops = extract_file_operations([], events)
        assert ops["modified"] == ["a.txt", "z.txt"]


# ── build_summary_payload ─────────────────────────────────────────────────


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


class TestBuildSummaryPayload:
    def test_previous_summary_preserved(self):
        payload = build_summary_payload("OLD SUMMARY", [], [])
        assert "OLD SUMMARY" in payload

    def test_context_stats_section(self):
        messages = [_msg("user", "hello world")]
        events = [_tool_event("read_file", {"path": "a.txt"})]
        payload = build_summary_payload("", messages, events)
        assert "[Context Stats]" in payload
        assert "total=" in payload
        assert "1 messages" in payload

    def test_files_touched_section(self):
        events = [
            _tool_event("read_file", {"path": "src/a.py"}),
            _tool_event("write_file", {"path": "src/b.py"}),
        ]
        payload = build_summary_payload("", [], events)
        assert "[Files Touched]" in payload
        assert "src/a.py" in payload
        assert "src/b.py" in payload
        assert "Modified (preserve exact paths" in payload

    def test_no_files_section_still_present(self):
        payload = build_summary_payload("", [], [])
        assert "[Files Touched]" in payload
        assert "(none)" in payload

    def test_keep_last_excludes_recent_messages(self):
        old_token = "OLD_MSG_XYZ"
        recent_token = "RECENT_MSG_XYZ"
        messages = [_msg("user", old_token), _msg("user", recent_token)]
        payload = build_summary_payload("", messages, [], keep_last=1)
        assert old_token in payload
        assert recent_token not in payload
        assert "Keep only the last 1 messages" in payload

    def test_long_item_keeps_head_and_tail(self):
        body = "A" * 3000
        tail_marker = "END_MARKER_12345"
        messages = [_msg("user", body + tail_marker)]
        payload = build_summary_payload("", messages, [])
        # Tail must survive the truncation
        assert tail_marker in payload
        # Head survives too
        assert body[:50] in payload
        assert "chars omitted" in payload

    def test_backward_compat_signature(self):
        # Calling without the new optional kwargs must still work
        messages = [_msg("user", "hi")]
        events = [_tool_event("write_file", {"path": "x.txt"})]
        payload = build_summary_payload("", messages, events)
        assert payload.startswith("[Previous Context Summary]")

    def test_explicit_file_ops_and_stats_override(self):
        messages = [_msg("user", "hello")]
        payload = build_summary_payload(
            "",
            messages,
            [],
            file_ops={"read": ["manual.txt"], "modified": []},
            token_stats={"messages": 1, "events": 0, "total": 1, "message_count": 1, "event_count": 0},
        )
        assert "manual.txt" in payload
        assert "total=1" in payload
