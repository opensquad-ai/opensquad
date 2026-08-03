"""Deterministic tests for _runner/_turn_loop.py::TurnLoop.

Drives the turn-result logic with a fake runner (no LLM, no gateway):
- tool-call path: fake tool_registry.call resolves synchronously
- plain-text path: <thought> + <to_user> response
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import opensquad.runner as runner_module
from opensquad._runner._turn_loop import TurnLoop


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
    runner.task_manager = MagicMock()
    runner.tool_registry = MagicMock()
    runner.tool_registry.call = AsyncMock(return_value="ok result")
    runner._plugin_manager = None
    runner._summarize_result = lambda name, result: result
    runner._truncate_result_text = lambda text, max_len: text
    runner._get_tool_output_max_chars = lambda: 4000
    runner._is_leaked_tool_params = lambda text: False
    runner._is_repeated_content = lambda text: False
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
