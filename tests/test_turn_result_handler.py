import asyncio
from types import SimpleNamespace

from opensquad.turn_result_handler import TurnResultHandler


class DummySessionManager:
    def __init__(self):
        self.events = []
        self.messages = []
        self.elapsed_ms = None
        self.title = None
        self.current_session_id = "sess-1"
        self.end_task_marked = False

    def add_event(self, event_type, payload, turn_id=None, round_id=None):
        self.events.append((event_type, payload, turn_id, round_id))

    def add_message(self, role, content, **extra):
        self.messages.append((role, content, extra))

    def update_last_message_elapsed_ms(self, elapsed_ms):
        self.elapsed_ms = elapsed_ms

    def mark_last_assistant_end_task(self):
        self.end_task_marked = True

    def set_title(self, title):
        self.title = title

    def get_current_session_id(self):
        return self.current_session_id

    def get_session_list(self):
        return [{"id": self.current_session_id, "title": self.title or "Untitled"}]


class DummyTaskManager:
    def __init__(self):
        self.updated = []

    def update(self, text):
        self.updated.append(text)


class DummyStateManager:
    def __init__(self):
        self.states = []
        self.wakes = []

    async def set_state(self, state):
        self.states.append(state)

    async def set_wake_mode(self, wake):
        self.wakes.append(wake)


class DummyPluginManager:
    def __init__(self):
        self.calls = []

    async def run_hook(self, name, ctx):
        self.calls.append((name, ctx))
        return ctx


class DummyBus:
    def __init__(self):
        self.events = []

    async def emit_async(self, event_type, payload):
        self.events.append((event_type, payload))


class DummyRunner:
    def __init__(self):
        self._current_turn = "turn-1"
        self._current_round = "round-1"
        self._current_input_source = "web"
        self._agent_id = "agent-1"
        self._workflow_started_ms = 0
        self._max_auto_continue_retries = 3
        self._auto_continue_retries = 0
        self._awaiting_user_reply = False
        self._last_user_msg_from_to_user = False
        self._last_user_input = "do a task"
        self._streamed_user_text = []
        self._streamed_user_tag = None
        self._plugin_manager = DummyPluginManager()
        self._session_manager = DummySessionManager()
        self._state_manager = DummyStateManager()
        self.task_manager = DummyTaskManager()
        self._bus = DummyBus()
        self.chat_api = SimpleNamespace(_prev_reasoning_content="why", enable_repetition_check=False)
        self.emitted = []

    async def _emit(self, event_type, payload):
        self.emitted.append((event_type, payload))

    def _extract_tag(self, response, tag):
        import re

        m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", response, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else None

    def _remove_tags(self, text, tags):
        import re

        result = text
        for tag in tags:
            result = re.sub(rf"<{tag}\\b[^>]*>.*?</{tag}>", "", result, flags=re.DOTALL | re.IGNORECASE)
            result = re.sub(rf"<{tag}\\b[^>]*/>", "", result, flags=re.IGNORECASE)
            result = re.sub(rf"<{tag}\\b[^>]*>.*", "", result, flags=re.DOTALL | re.IGNORECASE)
        return result.strip()

    def _remove_all_tags(self, text):
        import re

        return re.sub(r"<[^>]+>", "", text).strip()


def test_parse_and_persist_tags_records_plan_and_thought():
    runner = DummyRunner()
    handler = TurnResultHandler(runner)

    result = asyncio.run(
        handler.parse_and_persist_tags(
            "<thought>think</thought><plan>step1</plan><option>pick me</option><state>working</state>"
        )
    )

    assert result.thought_text == "think"
    assert result.plan_text == "step1"
    assert result.new_state == "working"
    assert runner.task_manager.updated == ["step1"]
    assert any(evt[0] == "thought" for evt in runner._session_manager.events)
    assert any(evt[0] == "plan" for evt in runner._session_manager.events)
    assert ("plan", {"id": runner.emitted[0][1]["id"], "text": "step1"}) == runner.emitted[0]


def test_extract_user_facing_message_prefers_streamed_content():
    runner = DummyRunner()
    runner._streamed_user_text = ["hello", " world"]
    runner._streamed_user_tag = "to_user_reply"
    handler = TurnResultHandler(runner)

    result = handler.extract_user_facing_message("<to_user>ignored</to_user>")

    assert result.user_msg == "hello world"
    assert result.user_msg_from_tag == "to_user_reply"
    assert runner._awaiting_user_reply is True


def test_extract_user_facing_message_prefers_end_task_tag():
    runner = DummyRunner()
    handler = TurnResultHandler(runner)

    result = handler.extract_user_facing_message(
        "<to_user>mid</to_user><to_user_end_task>final report</to_user_end_task>"
    )

    assert result.user_msg == "final report"
    assert result.user_msg_from_tag == "to_user_end_task"


def test_emit_user_facing_message_end_task_event():
    runner = DummyRunner()
    handler = TurnResultHandler(runner)
    user_message = SimpleNamespace(
        user_msg="final report",
        user_msg_from_tag="to_user_end_task",
        saved_msg=None,
        saved_output_media=None,
    )

    result = asyncio.run(handler.emit_user_facing_message(user_message, None))

    assert ("to_user_end_task", "final report") in runner.emitted
    assert runner._session_manager.end_task_marked is True
    assert result.saved_msg == "final report"


def test_finalize_without_tools_persists_message_and_waits():
    runner = DummyRunner()
    handler = TurnResultHandler(runner)
    user_message = SimpleNamespace(
        user_msg="done",
        user_msg_from_tag="to_user",
        saved_msg="done",
        saved_output_media=None,
    )

    result = asyncio.run(
        handler.finalize_without_tools(
            full_response="<to_user>done</to_user>",
            user_message=user_message,
            sys_cmd=None,
            finish_reason="stop",
            stream_error=False,
            tool_data_from_api=None,
        )
    )

    assert result == (False, "", True)
    assert runner._session_manager.messages == [("assistant", "done", {"reasoning_content": "why"})]
    assert runner._session_manager.elapsed_ms is not None


def test_finalize_without_tools_auto_continue_on_trailing_colon():
    runner = DummyRunner()
    handler = TurnResultHandler(runner)
    user_message = SimpleNamespace(
        user_msg="",
        user_msg_from_tag=None,
        saved_msg=None,
        saved_output_media=None,
    )

    result = asyncio.run(
        handler.finalize_without_tools(
            full_response="Need tool:",
            user_message=user_message,
            sys_cmd=None,
            finish_reason="stop",
            stream_error=False,
            tool_data_from_api=None,
        )
    )

    assert result == (
        False,
        "[System Prompt] You ended with a trailing colon, which usually means you intended to call a tool next. Continue immediately by calling the appropriate tool.",
        False,
    )
    assert runner._auto_continue_retries == 1
