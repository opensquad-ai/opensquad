# -*- coding: utf-8 -*-
import asyncio
from types import SimpleNamespace

import opensquad.tool_turn_executor as tool_turn_executor_module
from opensquad.parser import ResponseParser
from opensquad.tool_turn_executor import ToolTurnExecutor


class DummySessionManager:
    def __init__(self):
        self.events = []
        self.messages = []

    def add_event(self, event_type, payload, turn_id=None, round_id=None):
        self.events.append((event_type, payload, turn_id, round_id))

    def add_message(self, role, content, **extra):
        self.messages.append((role, content, extra))


class DummyPluginManager:
    def __init__(self):
        self.calls = []

    async def run_hook(self, name, ctx):
        self.calls.append((name, ctx))
        return ctx


class DummyToolRegistry:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    async def call(self, name, args):
        self.calls.append((name, args))
        return self.results[name]


class DummyChatApi:
    def __init__(self):
        self.tool_results = []
        self.pipeline_events = []
        self.model = "test-model"

    def add_tool_result(self, tool_name, tool_args, result, tool_call_id):
        self.tool_results.append(
            {
                "tool_name": tool_name,
                "tool_args": tool_args,
                "result": result,
                "tool_call_id": tool_call_id,
            }
        )

    def add_pipeline_events(self, events):
        self.pipeline_events.append(events)


class DummyEvent:
    def __init__(self, source, content="", metadata=None, formatted=None):
        self.source = source
        self.content = content
        self.metadata = metadata or {}
        self._formatted = formatted or f"[{source}] {content}".strip()

    def format_for_llm(self):
        return self._formatted


class DummyRunner:
    def __init__(self, tool_results=None):
        self._current_turn = "turn-1"
        self._current_round = "round-1"
        self._agent_id = "agent-1"
        self._agent_dir = ""
        self._last_user_input = "do a task"
        self._current_images = []
        self._plugin_manager = DummyPluginManager()
        self._session_manager = DummySessionManager()
        self.tool_registry = DummyToolRegistry(tool_results)
        self.chat_api = DummyChatApi()
        self.ResponseParser = ResponseParser
        self.logger = SimpleNamespace(info=lambda *args, **kwargs: None)
        self.emitted = []

    async def _emit(self, event_type, payload):
        self.emitted.append((event_type, payload))

    @staticmethod
    def _truncate_result_text(text, max_len):
        if max_len is None or len(text) <= max_len:
            return text
        return text[:max_len]

    @staticmethod
    def _get_tool_output_max_chars():
        return 50000


def test_execute_system_wait_returns_control_flow(monkeypatch):
    runner = DummyRunner(
        {
            "system.wait": {
                "status": "success",
                "wake_type": "event",
                "wake_reason": "message arrived",
                "actual_seconds": 3,
            }
        }
    )
    executor = ToolTurnExecutor(runner)

    monkeypatch.setattr(tool_turn_executor_module.event_pipeline, "drain_sync", lambda: [])

    result = asyncio.run(
        executor.execute(
            full_response="",
            tool_data_from_api=[("system.wait", {"seconds": 3})],
            saved_msg=None,
        )
    )

    assert result.handled is True
    assert result.return_value[0] is False
    assert "Wake-" in result.return_value[1]
    assert result.return_value[2] is False
    assert len(runner.chat_api.tool_results) == 1
    assert len(runner._session_manager.events) == 2
    assert runner.emitted[0][0] == "tool_call"
    assert runner.emitted[1][0] == "tool_result"


def test_execute_batch_commits_multiple_tool_results(monkeypatch):
    runner = DummyRunner(
        {
            "tool.alpha": "alpha-result",
            "tool.beta": "beta-result",
        }
    )
    executor = ToolTurnExecutor(runner)

    monkeypatch.setattr(tool_turn_executor_module.event_pipeline, "drain_sync", lambda: [])
    monkeypatch.setattr(tool_turn_executor_module.task_logger, "has_active_task", lambda: False)

    result = asyncio.run(
        executor.execute(
            full_response="",
            tool_data_from_api=[("tool.alpha", {"x": 1}), ("tool.beta", {"y": 2})],
            saved_msg="hello",
        )
    )

    assert result.handled is True
    assert result.return_value == (False, "", False)
    assert [entry["tool_name"] for entry in runner.chat_api.tool_results] == ["tool.alpha", "tool.beta"]
    tool_result_events = [evt for evt in runner._session_manager.events if evt[0] == "tool_result"]
    assert len(tool_result_events) == 2
    emitted_tool_results = [evt for evt in runner.emitted if evt[0] == "tool_result"]
    assert len(emitted_tool_results) == 2


def test_drain_pipeline_events_persists_user_messages_and_images(monkeypatch, tmp_path):
    runner = DummyRunner({"tool.alpha": "ok"})
    runner._agent_dir = str(tmp_path)
    executor = ToolTurnExecutor(runner)

    events = [
        DummyEvent("web", content="hello from web", formatted="[web] hello from web"),
        DummyEvent(
            "vision_tool",
            metadata={"action": "inject_images", "image_paths": ["img1.png", "img2.png"]},
            formatted="[vision] injected",
        ),
    ]
    monkeypatch.setattr(tool_turn_executor_module.event_pipeline, "drain_sync", lambda: events)
    monkeypatch.setattr(tool_turn_executor_module.task_logger, "has_active_task", lambda: False)

    result = asyncio.run(
        executor.execute(
            full_response="",
            tool_data_from_api=[("tool.alpha", {})],
            saved_msg=None,
        )
    )

    assert result.handled is True
    assert runner._session_manager.messages == [("user", "hello from web", {})]
    assert ("user_msg", "hello from web") in runner.emitted
    assert runner._current_images == ["img1.png", "img2.png"]
    assert len(runner.chat_api.pipeline_events) == 1
    assert "External Events" in runner.chat_api.pipeline_events[0]
