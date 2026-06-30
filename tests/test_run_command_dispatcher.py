from __future__ import annotations

import asyncio
from types import SimpleNamespace

from opensquad.run_command_dispatcher import RunCommandDispatcher


class DummyInputHub:
    def __init__(self):
        self.cleared = False
        self.pending = []

    def clear_stop_request(self):
        self.cleared = True

    def get_all_pending(self):
        return self.pending


class DummyBus:
    def __init__(self):
        self.events = []

    async def emit_async(self, event_type, payload):
        self.events.append((event_type, payload))


class DummySessionManager:
    def __init__(self):
        self.started = False
        self.loaded = None
        self.current_id = "sid-1"

    def start_new_session(self):
        self.started = True
        self.current_id = "sid-2"

    def get_current_session_id(self):
        return self.current_id

    def get_session_list(self):
        return [self.current_id]

    def load_history_session(self, sid):
        self.loaded = sid
        self.current_id = sid
        return True

    def get_messages(self):
        return []

    def get_events(self):
        return []


def build_runner():
    emitted = []

    async def emit(event_type, payload):
        emitted.append((event_type, payload))

    runner = SimpleNamespace(
        _input_hub=DummyInputHub(),
        _broadcast_token_stats=lambda: asyncio.sleep(0),
        _reset_session_stats=lambda: None,
        _pending_buffer=[],
        _session_manager=DummySessionManager(),
        _turn_sid="sid-1",
        _load_history=lambda: None,
        _emit=emit,
        _bus=DummyBus(),
        _current_input_source="web",
    )
    return runner, emitted


def test_dispatch_stop_command():
    runner, _ = build_runner()
    dispatcher = RunCommandDispatcher(runner)

    result = asyncio.run(dispatcher.dispatch("__STOP__"))

    assert result.handled is True
    assert result.should_continue is True
    assert runner._input_hub.cleared is True


def test_dispatch_resume_workflow_rewrites_query():
    runner, _ = build_runner()
    dispatcher = RunCommandDispatcher(runner)

    result = asyncio.run(dispatcher.dispatch("__RESUME_WORKFLOW__"))

    assert result.handled is False
    assert result.next_query == "Continue the previous task from where you left off."
    assert runner._current_input_source == "wake"


def test_dispatch_load_session_emits_sync_events():
    runner, emitted = build_runner()
    dispatcher = RunCommandDispatcher(runner)

    result = asyncio.run(dispatcher.dispatch("__LOAD_SESSION__:sid-9"))

    assert result.handled is True
    assert result.should_continue is True
    assert runner._session_manager.loaded == "sid-9"
    assert any(event[0] == "info" for event in emitted)
