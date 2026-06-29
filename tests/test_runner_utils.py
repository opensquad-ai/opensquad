# -*- coding: utf-8 -*-
"""Unit tests for runner.py pure utility functions.

Tests cover: _build_context_prefix, _filter_native_tokens, _remove_all_tags,
_extract_text_before_tool, _is_repeated_content, _truncate_result_text.
"""
import pytest


def _make_runner():
    """Create a minimal AgentRunner instance for testing instance methods."""
    from opensquad.runner import AgentRunner
    runner = object.__new__(AgentRunner)
    # _remove_all_tags calls self._filter_native_tokens which is a @staticmethod
    # but the instance still needs a _get_session_manager for _is_repeated_content
    import types
    runner._get_session_manager = types.MethodType(lambda self: None, runner)
    return runner


# ── _build_context_prefix ────────────────────────────────────────────────

class TestBuildContextPrefix:
    """Test _build_context_prefix — assembling dynamic context blocks."""

    @staticmethod
    def _target(dynamic_parts: dict) -> str:
        from opensquad.runner import _build_context_prefix
        return _build_context_prefix(dynamic_parts)

    def test_empty_parts(self):
        result = self._target({})
        assert result == ""

    def test_all_none(self):
        result = self._target({"RUNTIME_STATE": "", "TASK_STATE": None})
        assert result == ""

    def test_single_part(self):
        result = self._target({"RUNTIME_STATE": "idle"})
        assert "idle" in result
        assert "System Context" in result or "[System Context" in result

    def test_standard_order(self, sample_dynamic_parts):
        result = self._target(sample_dynamic_parts)
        parts = result.split("### ")
        assert any("Runtime State" in p for p in parts)
        assert "custom_key" in result

    def test_unknown_key_appended(self):
        parts = {"RUNTIME_STATE": "working", "NEW_KEY": "new_val"}
        result = self._target(parts)
        assert "NEW_KEY" in result
        assert "new_val" in result


# ── _filter_native_tokens ───────────────────────────────────────────────

class TestFilterNativeTokens:
    """Test _filter_native_tokens — strip leaked model-internal tokens."""

    @staticmethod
    def _target(text: str) -> str:
        from opensquad.runner import AgentRunner
        return AgentRunner._filter_native_tokens(text)

    def test_plain_text_passthrough(self):
        assert self._target("Hello world") == "Hello world"

    def test_empty_text(self):
        assert self._target("") == ""
        assert self._target(None) is None

    def test_qwen_tool_calls(self):
        text = '<|tool_calls_section_begin|>\n<|tool_call|>{"name":"read"}\n<|tool_calls_section_end|>Hello'
        result = self._target(text)
        assert "<|tool_calls" not in result
        assert result.strip() == "Hello"

    def test_kimi_function_format(self):
        text = 'functions.read_file:1{"path":"/etc/passwd"}Hello'
        result = self._target(text)
        assert "functions." not in result
        assert result.strip() == "Hello"


# ── _remove_all_tags ────────────────────────────────────────────────────

class TestRemoveAllTags:
    """Test _remove_all_tags — strip tool-related XML tags."""

    @staticmethod
    def _target(text: str) -> str:
        runner = _make_runner()
        return runner._remove_all_tags(text)

    def test_empty_text(self):
        assert self._target("") == ""

    def test_remove_thought_tag(self):
        text = "Before <thought>I think this</thought> After"
        result = self._target(text)
        assert "Before" in result
        assert "I think" not in result
        assert "After" in result

    def test_to_user_content_kept(self):
        text = "Intro <to_user>Hello there</to_user> Outro"
        result = self._target(text)
        assert "Hello there" in result
        assert "<to_user>" not in result

    def test_plain_text_passthrough(self):
        text = "Just some text without tags."
        result = self._target(text)
        assert result == "Just some text without tags."


# ── _extract_text_before_tool ────────────────────────────────────────────

class TestExtractTextBeforeTool:
    """Test _extract_text_before_tool — get text before first <tool_call>."""

    @staticmethod
    def _target(text: str):
        runner = _make_runner()
        return runner._extract_text_before_tool(text)

    def test_no_tool_call(self):
        result = self._target("Just text")
        assert result is None

    def test_text_before_tool(self):
        result = self._target("Please look up<tool_call name=\"read\">")
        # "Please look up" is 14 chars > 3, so it should be returned
        assert result == "Please look up"

    def test_too_short_text_before(self):
        # "Hi" is only 2 chars, less than the threshold of 3
        result = self._target("Hi<tool_call name=\"read\">")
        assert result is None

    def test_empty_input(self):
        assert self._target("") is None
        assert self._target(None) is None


# ── _truncate_result_text ───────────────────────────────────────────────

class TestTruncateResultText:
    """Test _truncate_result_text — truncate long tool results."""

    @staticmethod
    def _target(text: str, max_len: int) -> str:
        from opensquad.runner import AgentRunner
        return AgentRunner._truncate_result_text(text, max_len)

    def test_short_text_no_truncation(self):
        text = "Hello World"
        assert self._target(text, 100) == text

    def test_no_limit(self):
        text = "Hello World"
        assert self._target(text, 0) == text
        assert self._target(text, -1) == text

    def test_truncation_default(self):
        text = "A" * 2000
        result = self._target(text, 1500)
        assert len(result) < len(text)
        assert "truncated" in result

    def test_large_threshold(self):
        text = "A" * 60000
        result = self._target(text, 50001)
        assert "truncated" in result
        assert "A" in result


# ── _is_repeated_content ────────────────────────────────────────────────

class TestIsRepeatedContent:
    """Test _is_repeated_content — detect model output stuttering."""

    @staticmethod
    def _target(text: str) -> bool:
        runner = _make_runner()
        return runner._is_repeated_content(text)

    def test_short_text_not_repeated(self):
        assert self._target("Hello") is False

    def test_direct_repetition(self):
        assert self._target("Hello world. Hello world.") is True

    def test_no_repetition(self):
        assert self._target("The quick brown fox jumps over the lazy dog.") is False

    def test_triple_short_repetition(self):
        # "Go! Go! Go!" is 11 chars; the method checks len(text) < 15 first
        text = "Go! Go! Go!"
        assert self._target(text) is False

    def test_empty_text(self):
        assert self._target("") is False
        assert self._target(None) is False
