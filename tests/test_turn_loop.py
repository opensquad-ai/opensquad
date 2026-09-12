"""Deterministic tests for _runner/_turn_loop.py::TurnLoop.

Drives the turn-result logic with a fake runner (no LLM, no gateway):
- tool-call path: fake tool_registry.call resolves synchronously
- plain-text path: <thought> + <to_user> response
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import opensquad.runner as runner_module
from opensquad._runner._turn_loop import FORMAT_ERROR_MAX_STREAK, FORMAT_ERROR_STOP_HINT, TurnLoop

DOTS_DIRECTORY_TREE = (
    "<dots_function_call>\n"
    '<invoke name="mcp__filesystem__directory_tree">\n'
    '<parameter name="path">.</parameter>\n'
    "</invoke>\n"
    "</dots_function_call>"
)


def _make_fake_runner():
    """Minimal AgentRunner stand-in carrying only what the turn loop touches."""
    runner = MagicMock()
    runner._agent_id = "test-agent"
    runner._agent_dir = ""
    runner._current_turn = 1
    runner._current_round = 2
    runner._current_input_source = "test"
    runner._emit = AsyncMock()
    runner.chat_api = MagicMock()  # enable_repetition_check defaults False
    runner.chat_api.enable_repetition_check = False
    runner.task_manager = MagicMock()
    runner.tool_registry = MagicMock()
    runner.tool_registry.call = AsyncMock(return_value="ok result")
    runner._plugin_manager = None
    runner._summarize_result = lambda name, result: result
    runner._truncate_result_text = lambda text, max_len: text
    runner._get_tool_output_max_chars = lambda: 4000
    runner._is_leaked_tool_params = lambda text: False
    runner._is_repeated_content = lambda text: False
    runner._streamed_user_text = []
    runner._streamed_user_tag = None
    runner._format_error_streak = 0
    runner._last_user_input = ""
    runner._in_task = False
    runner._awaiting_user_reply = False
    runner._last_user_msg_from_to_user = False
    runner._auto_continue_retries = 0
    # Real tag helpers stay bound to the real implementations
    runner._extract_tag = runner_module.AgentRunner._extract_tag.__get__(runner, runner_module.AgentRunner)
    runner._filter_native_tokens = runner_module.AgentRunner._filter_native_tokens
    runner._remove_all_tags = runner_module.AgentRunner._remove_all_tags.__get__(runner, runner_module.AgentRunner)
    return runner


@pytest.fixture
def turn_loop(monkeypatch):
    """TurnLoop bound to a fake runner; patches module-level singletons."""
    fake = _make_fake_runner()
    fake_sm = MagicMock()
    fake_sm.get_session_list.return_value = []
    fake_sm.get_current_session_id.return_value = "test-session"
    fake._injected_session_manager = fake_sm
    fake._injected_state_manager = MagicMock()
    monkeypatch.setattr(runner_module, "_active_runner", fake)
    loop = TurnLoop(fake)
    return loop, fake, fake_sm


async def test_plain_text_turn_stops_and_persists_thought(turn_loop):
    loop, fake, fake_sm = turn_loop
    response = "<thought>I should reply briefly.</thought><to_user>Hello there</to_user>"

    stop, next_input, went_to_sleep = await loop.handle_turn_result(response)

    assert stop is True
    assert next_input == ""
    assert went_to_sleep is False
    # thought persisted to session
    fake_sm.add_event.assert_any_call(
        "thought",
        {"text": "I should reply briefly."},
        turn_id=1,
        round_id=2,
    )
    # user-visible text emitted
    to_user_calls = [c.args[0] for c in fake._emit.await_args_list if c.args and c.args[0] == "to_user_final"]
    assert to_user_calls, "expected a to_user_final emission"
    assert "Hello there" in str(fake._emit.await_args_list[-1].args[1])


async def test_tool_call_executes_via_registry_and_returns_continue(turn_loop):
    loop, fake, fake_sm = turn_loop
    response = "<thought>Calling a tool.</thought>"
    tool_data = [("system.echo", {"text": "hi"})]

    stop, next_input, went_to_sleep = await loop.handle_turn_result(response, tool_data_from_api=tool_data)

    # tool executed
    fake.tool_registry.call.assert_awaited_once_with("system.echo", {"text": "hi"})
    # tool_call + tool_result events persisted and emitted
    emitted_types = [c.args[0] for c in fake._emit.await_args_list if c.args]
    assert "tool_call" in emitted_types
    assert "tool_result" in emitted_types
    # add_event is synchronous -> call_args_list, not await_args_list
    types_persisted = [c.args[0] for c in fake_sm.add_event.call_args_list if c.args]
    assert types_persisted.count("tool_call") >= 1
    assert types_persisted.count("tool_result") >= 1
    # no stop requested after tool execution
    assert stop is False
    assert next_input == ""


async def test_tool_result_includes_return_value(turn_loop):
    loop, fake, fake_sm = turn_loop
    await loop.handle_turn_result("<to_user>done</to_user>", tool_data_from_api=[("system.echo", {"text": "hi"})])
    result_events = [c.args[1] for c in fake_sm.add_event.call_args_list if c.args and c.args[0] == "tool_result"]
    assert result_events, "expected at least one tool_result persisted"
    assert any("ok result" in str(r.get("result", "")) for r in result_events)


async def test_stop_requested_cancels_remaining_tools(turn_loop, monkeypatch):
    loop, fake, fake_sm = turn_loop

    async def stop_requested():
        return True

    from opensquad.input_hub import input_hub as _hub

    monkeypatch.setattr(_hub, "is_stop_requested", stop_requested)
    monkeypatch.setattr(_hub, "is_session_stop_requested", lambda sid: False)

    await loop.handle_turn_result(
        "<to_user>x</to_user>",
        tool_data_from_api=[("tool.a", {}), ("tool.b", {})],
    )

    # First tool skipped due to stop; remaining ones cancelled with marker
    fake.tool_registry.call.assert_not_awaited()
    emitted = [c.args[1] for c in fake._emit.await_args_list if c.args and c.args[0] == "tool_result"]
    assert emitted and any("Cancelled" in str(e.get("result", "")) for e in emitted)


def _bind_real_leak_guard(fake):
    fake._is_leaked_tool_params = runner_module.AgentRunner._is_leaked_tool_params.__get__(
        fake, runner_module.AgentRunner
    )


async def test_dots_tool_call_executes_even_when_stream_left_a_json_leak(turn_loop):
    """Parse the raw response first; a streamed JSON leftover must not format_error."""
    loop, fake, fake_sm = turn_loop
    _bind_real_leak_guard(fake)
    fake._streamed_user_text = ['{"path": "."}']

    stop, next_input, went_to_sleep = await loop.handle_turn_result(DOTS_DIRECTORY_TREE)

    fake.tool_registry.call.assert_awaited()
    called_name = fake.tool_registry.call.await_args.args[0]
    assert called_name == "mcp__filesystem__directory_tree"
    emitted_names = [c.args[1].get("name") for c in fake._emit.await_args_list if c.args and c.args[0] == "tool_call"]
    assert "format_error" not in emitted_names
    assert stop is False
    assert went_to_sleep is False
    assert next_input == ""


async def test_hollow_dots_stream_executes_full_response_tools(turn_loop):
    loop, fake, fake_sm = turn_loop
    _bind_real_leak_guard(fake)
    fake._streamed_user_text = ["<dots_function_call>\n\n</dots_function_call>"]

    stop, next_input, _ = await loop.handle_turn_result(DOTS_DIRECTORY_TREE)

    fake.tool_registry.call.assert_awaited()
    assert fake.tool_registry.call.await_args.args[0] == "mcp__filesystem__directory_tree"
    emitted_names = [c.args[1].get("name") for c in fake._emit.await_args_list if c.args and c.args[0] == "tool_call"]
    assert "format_error" not in emitted_names
    assert stop is False
    assert next_input == ""


async def test_format_error_stops_after_max_streak(turn_loop):
    loop, fake, fake_sm = turn_loop
    _bind_real_leak_guard(fake)
    leaked = "<message>hello</message>"
    fake._streamed_user_text = [leaked]

    for i in range(FORMAT_ERROR_MAX_STREAK - 1):
        stop, next_input, went_to_sleep = await loop.handle_turn_result(leaked)
        assert stop is False, f"streak {i + 1} should retry"
        assert went_to_sleep is False
        assert "Error:" in next_input
        assert fake._format_error_streak == i + 1

    stop, next_input, went_to_sleep = await loop.handle_turn_result(leaked)
    assert stop is True
    assert next_input == ""
    assert went_to_sleep is False
    assert fake._format_error_streak == FORMAT_ERROR_MAX_STREAK
    finals = [c.args[1] for c in fake._emit.await_args_list if c.args and c.args[0] == "to_user_final"]
    assert any(FORMAT_ERROR_STOP_HINT in str(text) for text in finals)
    fake.tool_registry.call.assert_not_awaited()
