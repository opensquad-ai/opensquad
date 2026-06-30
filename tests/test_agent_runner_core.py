"""
AgentRunner core loop tests.

Validates P0.3 (DI via AgentContext) + P0.5 (test fixture).
Covers pure methods and the main run() loop with mocked dependencies.
"""

import json
import os
from unittest.mock import patch

import pytest

from opensquad._context import get_current_context
from opensquad.runner import AgentRunner

pytestmark = pytest.mark.asyncio

# ===================================================================
# Helper: minimal runner with mocked deps (for method-level tests)
# ===================================================================


@pytest.fixture
def minimal_runner(application_context):
    """Create an AgentRunner with all mocks for testing methods that need self."""
    ctx = application_context
    runner = AgentRunner(
        chat_api=ctx.chat_api,
        tool_registry=ctx.tool_registry,
        agent_context=ctx,
    )
    return runner


# ===================================================================
# Pure method tests (instance methods, need a runner)
# ===================================================================


class TestIsLeakedToolParams:
    """Runner._is_leaked_tool_params — instance method, no side effects."""

    def test_empty_text_returns_false(self, minimal_runner):
        assert minimal_runner._is_leaked_tool_params("") is False
        assert minimal_runner._is_leaked_tool_params("   ") is False

    def test_plain_text_returns_false(self, minimal_runner):
        assert minimal_runner._is_leaked_tool_params("Hello, how can I help?") is False

    def test_bare_json_object_is_leaked(self, minimal_runner):
        assert minimal_runner._is_leaked_tool_params("{}") is True
        assert minimal_runner._is_leaked_tool_params('{"key": "value"}') is True

    def test_text_with_json_inside_is_not_leaked(self, minimal_runner):
        assert minimal_runner._is_leaked_tool_params('I will call read_file with {"path": "/tmp"}') is False

    def test_xml_tool_call_is_not_leaked(self, minimal_runner):
        xml = '<tool_call name="system.echo"><message>hello</message></tool_call>'
        assert minimal_runner._is_leaked_tool_params(xml) is False


class TestFilterNativeTokens:
    """Runner._filter_native_tokens — static method, pure."""

    def test_qwen3_tokens_removed(self):
        text = "<|tool_calls_section_begin|><|tool|>...<|tool_calls_section_end|>Hello"
        result = AgentRunner._filter_native_tokens(text)
        assert "<|tool_calls_section_begin|>" not in result
        assert "Hello" in result

    def test_plain_text_unchanged(self):
        text = "Hello, this is a normal response."
        assert AgentRunner._filter_native_tokens(text) == text

    def test_kimi_function_call_removed(self):
        text = 'functions.read_file:0{"path": "/tmp/test.txt"}output'
        result = AgentRunner._filter_native_tokens(text)
        assert "functions" not in result
        assert "output" in result


class TestValidateMessageSequence:
    """Runner._validate_message_sequence — mutates chat_api.req list."""

    def test_empty_req_no_change(self, application_context):
        ctx = application_context
        ctx.chat_api.req = []
        runner = AgentRunner(
            chat_api=ctx.chat_api,
            tool_registry=ctx.tool_registry,
            agent_context=ctx,
        )
        runner._validate_message_sequence()
        assert runner.chat_api.req == []

    def test_orphan_tool_removed(self, application_context):
        ctx = application_context
        ctx.chat_api.req = [{"role": "tool", "content": "orphan"}]
        runner = AgentRunner(
            chat_api=ctx.chat_api,
            tool_registry=ctx.tool_registry,
            agent_context=ctx,
        )
        runner._validate_message_sequence()
        tool_msgs = [m for m in runner.chat_api.req if m.get("role") == "tool"]
        assert len(tool_msgs) == 0


class TestAgentRunnerDI:
    """Verify AgentContext-based dependency injection (P0.3 + P0.5)."""

    def test_creates_with_context(self, application_context):
        ctx = application_context
        runner = AgentRunner(
            chat_api=ctx.chat_api,
            tool_registry=ctx.tool_registry,
            agent_context=ctx,
        )
        assert runner._ctx is ctx
        assert runner._state_manager is ctx.state_manager
        assert runner._session_manager is ctx.session_manager
        assert runner._bus is ctx.event_bus
        assert runner._input_hub is ctx.input_hub
        assert runner._message_queue is ctx.message_queue
        assert runner.chat_api is ctx.chat_api
        assert runner.tool_registry is ctx.tool_registry

    def test_creates_without_context_fallback(self, application_context):
        ctx = application_context
        runner = AgentRunner(
            chat_api=ctx.chat_api,
            tool_registry=ctx.tool_registry,
        )
        assert runner._ctx is ctx


class TestConfigHotReload:
    """Runner._check_config_hot_reload — config change detection."""

    @pytest.mark.asyncio
    async def test_no_config_path_returns_early(self, minimal_runner):
        await minimal_runner._check_config_hot_reload()

    @pytest.mark.asyncio
    async def test_config_file_not_found_returns_early(self, application_context):
        ctx = application_context
        runner = AgentRunner(
            chat_api=ctx.chat_api,
            tool_registry=ctx.tool_registry,
            agent_context=ctx,
            config_path="/nonexistent/path/config.json",
        )
        await runner._check_config_hot_reload()

    @pytest.mark.asyncio
    async def test_config_mtime_unchanged_skips_reload(self, application_context, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"tools": ["system"]}), encoding="utf-8")
        ctx = application_context
        runner = AgentRunner(
            chat_api=ctx.chat_api,
            tool_registry=ctx.tool_registry,
            agent_context=ctx,
            config_path=str(cfg),
        )
        mtime_before = os.path.getmtime(str(cfg))
        assert runner._config_mtime == mtime_before
        await runner._check_config_hot_reload()
        assert runner._agent_tool_names == []

    @pytest.mark.asyncio
    async def test_config_tools_change_triggers_reload(self, application_context, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"tools": ["filesystem"]}), encoding="utf-8")
        ctx = application_context
        runner = AgentRunner(
            chat_api=ctx.chat_api,
            tool_registry=ctx.tool_registry,
            agent_context=ctx,
            config_path=str(cfg),
        )
        mtime_1 = runner._config_mtime
        import time as _time

        _time.sleep(0.02)
        cfg.write_text(json.dumps({"tools": ["filesystem", "system"]}), encoding="utf-8")
        with patch("opensquad.agents_boot.register_builtin_tools_sync"):
            await runner._check_config_hot_reload()
            assert runner._agent_tool_names == ["filesystem", "system"]
            assert runner._config_mtime > mtime_1


class TestContextManager:
    """P0.5: AgentContext fixture and utilities work correctly."""

    def test_application_context_provides_all_fields(self, application_context):
        ctx = application_context
        assert ctx.event_bus is not None
        assert ctx.input_hub is not None
        assert ctx.message_queue is not None
        assert ctx.state_manager is not None
        assert ctx.session_manager is not None
        assert ctx.chat_api is not None
        assert ctx.tool_registry is not None
        assert ctx.agent_id == "test-agent"
        assert ctx.agent_name == "Test Agent"

    def test_set_current_context_works(self, application_context):
        ctx = application_context
        retrieved = get_current_context()
        assert retrieved is ctx

    def test_application_context_is_complete(self, application_context):
        ctx = application_context
        assert ctx.is_complete is True
