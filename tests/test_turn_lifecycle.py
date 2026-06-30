from __future__ import annotations

import asyncio
from types import SimpleNamespace

from opensquad.turn_lifecycle import TurnLifecycle


class DummyContextBuilder:
    def __init__(self, has_prompt_snapshot: bool = False):
        self.has_prompt_snapshot = has_prompt_snapshot
        self.marked = False

    async def build(self, **kwargs):
        return (
            "system prompt",
            "dynamic prefix",
            {"tools": ["tool-a"], "tool_choice": "required"},
            True,
        )

    def mark_snapshot_emitted(self):
        self.marked = True


class DummySessionManager:
    def __init__(self):
        self.events = []

    def add_event(self, event_type, payload, turn_id=None, round_id=None):
        self.events.append((event_type, payload, turn_id, round_id))


def test_turn_lifecycle_applies_prompt_setup():
    emitted = []
    runner = SimpleNamespace(
        _context_builder=DummyContextBuilder(has_prompt_snapshot=False),
        _last_user_input="hello",
        _current_input_source="web",
        _current_turn=2,
        _current_round=4,
        _current_tools=None,
        _current_tool_choice="auto",
        _dynamic_context_prefix="",
        _session_manager=DummySessionManager(),
        _build_prompt_diff=lambda final, is_changed: ["diff"],
    )
    lifecycle = TurnLifecycle(runner)

    async def emit(event_type, payload):
        emitted.append((event_type, payload))

    result = asyncio.run(lifecycle.apply_prompt_setup(emit))

    assert result.tool_choice == "required"
    assert runner._current_tools == ["tool-a"]
    assert runner._current_tool_choice == "required"
    assert runner._dynamic_context_prefix == "dynamic prefix"
    assert emitted[0][0] == "prompt_update"
    assert runner._context_builder.marked is True
    assert runner._session_manager.events[0][0] == "prompt_update"


def test_turn_lifecycle_before_task_advances_memory_and_loads_history():
    class DummyMemory:
        def __init__(self):
            self.count = 0

        def advance_turn(self):
            self.count += 1

    memory = DummyMemory()
    runner = SimpleNamespace(
        _memory_manager=memory,
        chat_api=SimpleNamespace(load_his=None),
        _extract_task_id=lambda query: ("task-1", query.replace("<task-1>", "")),
    )
    lifecycle = TurnLifecycle(runner)

    query, task_id = lifecycle.before_task("<task-1>do work")

    assert query == "do work"
    assert task_id == "task-1"
    assert runner.chat_api.load_his == "task-1"
    assert memory.count == 1
