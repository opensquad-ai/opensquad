from __future__ import annotations

import asyncio
from types import SimpleNamespace

from opensquad.llm_call_orchestrator import LlmCallOrchestrator


class DummyChatApi:
    def __init__(self):
        self.req = []
        self.model = "demo"


class DummyInputHub:
    def __init__(self, stop_requested=False):
        self.stop_requested = stop_requested
        self.cleared = False

    def is_stop_requested(self):
        return self.stop_requested

    def clear_stop_request(self):
        self.cleared = True
        self.stop_requested = False


def build_runner():
    emitted = []

    async def emit(event_type, payload):
        emitted.append((event_type, payload))

    runner = SimpleNamespace(
        _current_attachments=[],
        _current_images=[],
        _tool_result_images=[],
        _is_img_mode=False,
        _emit=emit,
        _plugin_manager=None,
        chat_api=DummyChatApi(),
        _agent_id="agent-1",
        _current_tools=None,
        _current_tool_choice="auto",
        tool_call_strategy=SimpleNamespace(),
        _input_hub=DummyInputHub(),
        _streamed_user_text=[],
        _session_manager=SimpleNamespace(add_message=lambda role, content: emitted.append((role, content))),
    )
    return runner, emitted


def test_prepare_call_injects_attachment_lines():
    runner, _ = build_runner()
    runner._current_attachments = [
        {"type": "file", "original_name": "note.txt", "path": "/tmp/note.txt"},
        {"is_audio": True, "original_name": "memo.wav", "path": "/tmp/memo.wav"},
    ]
    orchestrator = LlmCallOrchestrator(runner)

    prepared = asyncio.run(orchestrator.prepare_call("User input", 1))

    assert "[Attachments]" in prepared.current_input
    assert "/tmp/note.txt" in prepared.current_input
    assert "/tmp/memo.wav" in prepared.current_input
    assert prepared.audio_paths == ["/tmp/memo.wav"]


def test_normalize_response_supports_dict_payload():
    runner, _ = build_runner()
    orchestrator = LlmCallOrchestrator(runner)

    normalized = asyncio.run(
        orchestrator.normalize_response(
            {
                "text": "hello",
                "tool_data": [("tool", {"x": 1})],
                "output_media": [{"type": "image"}],
                "finish_reason": "stop",
                "stream_error": True,
            }
        )
    )

    assert normalized.response_text == "hello"
    assert normalized.tool_data_from_api == [("tool", {"x": 1})]
    assert normalized.output_media == [{"type": "image"}]
    assert normalized.finish_reason == "stop"
    assert normalized.stream_error is True


def test_handle_stop_after_response_persists_partial_message():
    runner, emitted = build_runner()
    runner._input_hub = DummyInputHub(stop_requested=True)
    runner._streamed_user_text = ["partial", " reply"]
    orchestrator = LlmCallOrchestrator(runner)

    stopped = asyncio.run(orchestrator.handle_stop_after_response())

    assert stopped is True
    assert runner._input_hub.cleared is True
    assert ("assistant", "partial reply") in emitted
