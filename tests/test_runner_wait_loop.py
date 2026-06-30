from __future__ import annotations

import asyncio
from types import SimpleNamespace

from opensquad.runner_wait_loop import RunnerWaitLoop


class DummyInputHub:
    def __init__(self, pending=None, urgent=None, stop_requested=False):
        self.pending = pending or []
        self.urgent = urgent or []
        self.stop_requested = stop_requested
        self.cleared = False

    def is_stop_requested(self):
        return self.stop_requested

    def clear_stop_request(self):
        self.cleared = True
        self.stop_requested = False

    def check_urgent_commands(self):
        urgent = self.urgent
        self.urgent = []
        return urgent

    def get_all_pending(self):
        pending = self.pending
        self.pending = []
        return pending


class DummyChatApi:
    def __init__(self):
        self.req = []
        self.user_messages = []
        self.pipeline_events = []

    def add_user_message(self, content):
        self.user_messages.append(content)

    def add_pipeline_events(self, content):
        self.pipeline_events.append(content)


class DummySessionManager:
    def __init__(self):
        self.events = []
        self.messages = []

    def add_event(self, event_type, payload, turn_id=None, round_id=None):
        self.events.append((event_type, payload, turn_id, round_id))

    def add_message(self, role, content):
        self.messages.append((role, content))


def test_wait_loop_stops_on_stop_request():
    emitted = []

    async def emit(event_type, payload):
        emitted.append((event_type, payload))

    runner = SimpleNamespace(
        _session_manager=DummySessionManager(),
        _current_turn=1,
        _current_round=1,
        _emit=emit,
        _input_hub=DummyInputHub(stop_requested=True),
        _command_dispatcher=SimpleNamespace(),
        chat_api=DummyChatApi(),
        _setup_prompt=lambda: asyncio.sleep(0),
        _message_queue=SimpleNamespace(get_all=lambda: []),
    )
    wait_loop = RunnerWaitLoop(runner)

    result = asyncio.run(wait_loop.wait_for_events(None, "current"))

    assert result.task_finished is True
    assert runner._input_hub.cleared is True
    assert any(event[0] == "status" for event in emitted)


def test_wait_loop_persists_idle_supplement_as_user_message():
    emitted = []

    async def emit(event_type, payload):
        emitted.append((event_type, payload))

    chat_api = DummyChatApi()
    runner = SimpleNamespace(
        _session_manager=DummySessionManager(),
        _current_turn=1,
        _current_round=1,
        _emit=emit,
        _input_hub=DummyInputHub(pending=[{"content": "hello from wait"}]),
        _command_dispatcher=SimpleNamespace(),
        chat_api=chat_api,
        _setup_prompt=lambda: asyncio.sleep(0),
        _message_queue=SimpleNamespace(get_all=lambda: []),
        _inner_loop_count=0,
        _turn_start_time=0.0,
    )
    wait_loop = RunnerWaitLoop(runner)

    result = asyncio.run(wait_loop.wait_for_events(None, "current"))

    assert result.task_finished is False
    assert result.should_continue_turn_loop is True
    assert chat_api.user_messages == ["hello from wait"]
    assert runner._session_manager.messages == [("user", "hello from wait")]
