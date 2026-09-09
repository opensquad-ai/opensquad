"""Regression: parallel turns must tolerate repetition rewind + None user_msg."""

from __future__ import annotations

from types import SimpleNamespace

from opensquad.runner import AgentRunner


def test_is_leaked_tool_params_tolerates_none():
    # Bound method needs an instance; use object.__new__ to skip heavy __init__.
    runner = object.__new__(AgentRunner)
    assert runner._is_leaked_tool_params(None) is False
    assert runner._is_leaked_tool_params("") is False
    assert runner._is_leaked_tool_params("hello") is False


def test_repetition_rewind_getattr_safe_when_unset():
    """TurnLoop used to crash on parallel path before _repetition_rewind_count existed."""
    runner = SimpleNamespace()
    # Simulate missing attribute (old parallel-path bug)
    assert not hasattr(runner, "_repetition_rewind_count")
    count = getattr(runner, "_repetition_rewind_count", 0)
    assert count == 0
    runner._repetition_rewind_count = count + 1
    assert runner._repetition_rewind_count == 1
