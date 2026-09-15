"""Unit tests for runner pure utility functions.

Tests cover: build_context_prefix (moved to context_builder.py in P1-1),
_filter_native_tokens, _remove_all_tags, _extract_text_before_tool,
_is_repeated_content, _truncate_result_text.
"""


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
    """Test build_context_prefix — assembling dynamic context blocks."""

    @staticmethod
    def _target(dynamic_parts: dict) -> str:
        from opensquad.context_builder import build_context_prefix

        return build_context_prefix(dynamic_parts)

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

    def test_inline_thought_keeps_content(self):
        # Only *line-start* <thought>/<think> blocks are dropped (see
        # xml_parser.strip_prompted_thought_blocks). An inline one is a plain
        # tag-strip so prose is never hole-punched — "I think" must survive.
        text = "Before <thought>I think this</thought> After"
        result = self._target(text)
        assert result == "Before I think this After"

    def test_line_start_thought_block_dropped(self):
        assert self._target("<thought>secret</thought>visible") == "visible"
        assert self._target("line1\n<thought>secret</thought>\nline3") == "line1\n\nline3"

    def test_silent_protocol_block_dropped_with_content(self):
        assert self._target("A <plan>secret</plan> B") == "A  B"

    def test_to_user_content_kept(self):
        text = "Intro <to_user>Hello there</to_user> Outro"
        result = self._target(text)
        assert "Hello there" in result
        assert "<to_user>" not in result

    def test_plain_text_passthrough(self):
        text = "Just some text without tags."
        result = self._target(text)
        assert result == "Just some text without tags."

    def test_timeout_block_removed_not_inner_kept(self):
        assert self._target("<timeout>60</timeout>") == ""
        assert "60" not in self._target("Keep <timeout>60</timeout> me")
        assert "Keep" in self._target("Keep <timeout>60</timeout> me")
        assert "me" in self._target("Keep <timeout>60</timeout> me")
        assert self._target("<sleep>5</sleep>") == ""
        assert self._target("<to_system>task_complete</to_system>") == ""


class TestComposeUserVisibleMessage:
    """Protocol XML must not become the visible assistant reply."""

    @staticmethod
    def _target(text: str):
        from opensquad._runner._tag_utils import compose_user_visible_message

        return compose_user_visible_message(text)

    def test_timeout_only_is_empty(self):
        text, tag = self._target("<timeout>60</timeout>")
        assert text == ""
        assert tag is None

    def test_timeout_after_to_user_dropped(self):
        text, tag = self._target("<to_user>开始执行。</to_user>\n<timeout>60</timeout>")
        assert text == "开始执行。"
        assert tag == "to_user"


# ── _remove_tags / _extract_tag ─────────────────────────────────────────


class TestRemoveTags:
    """Test _remove_tags — strip named XML blocks including incomplete tags."""

    @staticmethod
    def _target(text: str, tags: list) -> str:
        runner = _make_runner()
        return runner._remove_tags(text, tags)

    def test_remove_named_block(self):
        result = self._target("Keep <plan>secret</plan> me", ["plan"])
        assert result == "Keep  me"

    def test_incomplete_tag_truncated(self):
        result = self._target('Hello <tool_call name="x">{"a": 1}', ["tool_call"])
        assert result == "Hello"
        assert "a" not in result


class TestExtractTag:
    """Test _extract_tag — pull inner text from a named XML tag."""

    @staticmethod
    def _target(text: str, tag: str):
        runner = _make_runner()
        return runner._extract_tag(text, tag)

    def test_extract_closed_tag(self):
        assert self._target("<state>idle</state>", "state") == "idle"

    def test_extract_unclosed_tag(self):
        assert self._target("<title>Hello world", "title") == "Hello world"

    def test_missing_tag_returns_none(self):
        assert self._target("no tags here", "state") is None


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
        result = self._target('Please look up<tool_call name="read">')
        # "Please look up" is 14 chars > 3, so it should be returned
        assert result == "Please look up"

    def test_too_short_text_before(self):
        # "Hi" is only 2 chars, less than the threshold of 3
        result = self._target('Hi<tool_call name="read">')
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


def test_looks_like_auth_failure_detects_credits_401():
    from opensquad.runner import _looks_like_auth_failure

    assert _looks_like_auth_failure(
        "[Error: AuthenticationError - Error code: 401 - CreditsError Insufficient balance]"
    )
    assert not _looks_like_auth_failure("ok, I'll reply in the group")
