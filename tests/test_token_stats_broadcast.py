"""token_stats broadcast must carry a resolvable sid and prefer session ChatAPI."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeChatApi:
    def __init__(self, *, token_max=128000, req=None, model="m"):
        self.token_max = token_max
        self.req = list(req or [{"role": "system", "content": "sys"}])
        self.model = model
        self.encoding = None
        self.history_dir = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_requests = 0
        self.total_cache_read_tokens = 0
        self.total_cache_creation_tokens = 0
        self._last_tools = None
        self.counted_contents: list[list] = []

    def _count_tokens(self, messages, tools=None):
        self.counted_contents.append([m.get("content") for m in (messages or []) if isinstance(m, dict)])
        return sum(len(str(m.get("content") or "")) for m in (messages or []) if isinstance(m, dict))


def _bind_token_stats_methods(runner):
    from opensquad.runner import AgentRunner

    runner._resolve_token_stats_sid = AgentRunner._resolve_token_stats_sid.__get__(runner, type(runner))
    runner._chat_api_for_token_stats = AgentRunner._chat_api_for_token_stats.__get__(runner, type(runner))
    runner._chat_api_owns_session = AgentRunner._chat_api_owns_session.__get__(runner, type(runner))
    runner._req_for_token_stats = AgentRunner._req_for_token_stats.__get__(runner, type(runner))
    runner._token_stats_usage_stamp = AgentRunner._token_stats_usage_stamp.__get__(runner, type(runner))
    runner._broadcast_token_stats = AgentRunner._broadcast_token_stats.__get__(runner, type(runner))


def _runner(*, chat_api, session_apis=None, turn_sid=""):
    return SimpleNamespace(
        _turn_sid=turn_sid,
        _fallback_turn_sid=turn_sid,
        _agent_id="agent-a",
        _session_chat_apis=session_apis or {},
        chat_api=chat_api,
        _hist_input_tokens=0,
        _hist_output_tokens=0,
        _hist_requests=0,
        _hist_cache_read_tokens=0,
        _hist_cache_creation_tokens=0,
        _token_stats_cache={},
        tool_registry=None,
        _current_tools=None,
        _tools_for_token_stats=lambda: None,
    )


@pytest.mark.asyncio
async def test_broadcast_token_stats_falls_back_sid_and_emits():
    root_api = _FakeChatApi()
    session_api = _FakeChatApi(token_max=200000, req=[{"role": "user", "content": "hi"}])

    runner = _runner(chat_api=root_api, session_apis={"sess-1": session_api})
    _bind_token_stats_methods(runner)

    sm = MagicMock()
    sm.get_focused_session_id.return_value = "sess-1"
    sm.get_current_session_id.return_value = "sess-1"
    sm.ensure_session_loaded.return_value = {}

    emitted = []

    async def _emit(event, payload):
        emitted.append((event, payload))

    with (
        patch("opensquad.runner._get_session_manager", return_value=sm),
        patch("opensquad.runner.bus") as bus,
        patch(
            "opensquad.token_breakdown.compute_token_breakdown",
            return_value={
                "system": 10,
                "user": 32,
                "tool": 0,
                "tool_defs": 0,
                "thought": 0,
                "overhead": 0,
                "response": 0,
            },
        ),
    ):
        bus.emit_async = AsyncMock(side_effect=_emit)
        await runner._broadcast_token_stats()

    assert emitted, "expected token_stats emit"
    event, payload = emitted[0]
    assert event == "token_stats"
    assert payload["sid"] == "sess-1"
    assert payload["data"]["max"] == 200000
    assert payload["data"]["used"] == len("hi")
    assert payload["data"]["session_id"] == "sess-1"


def test_resolve_token_stats_sid_filters_unknown():
    from opensquad.runner import AgentRunner

    runner = SimpleNamespace(_turn_sid="unknown")
    runner._resolve_token_stats_sid = AgentRunner._resolve_token_stats_sid.__get__(runner, type(runner))
    sm = MagicMock()
    sm.get_focused_session_id.return_value = ""
    sm.get_current_session_id.return_value = "real-sid"
    with patch("opensquad.runner._get_session_manager", return_value=sm):
        assert runner._resolve_token_stats_sid() == "real-sid"


def _breakdown():
    return {
        "system": 10,
        "user": 32,
        "tool": 0,
        "tool_defs": 0,
        "thought": 0,
        "overhead": 0,
        "response": 0,
    }


@pytest.mark.asyncio
async def test_history_session_stats_use_disk_not_current_req():
    """Switching to a history tab must not reuse the working session's context %."""
    current_req = [{"role": "user", "content": "CURRENT_LONG_CONTEXT" * 40}]
    history_msgs = [{"role": "user", "content": "short-history"}]
    root_api = _FakeChatApi(req=current_req)
    runner = _runner(chat_api=root_api, turn_sid="sess-current")
    _bind_token_stats_methods(runner)

    sm = MagicMock()
    sm.get_focused_session_id.return_value = "sess-current"
    sm.get_current_session_id.return_value = "sess-current"
    sm.ensure_session_loaded.return_value = {"messages": history_msgs, "events": []}

    emitted = []

    async def _emit(event, payload):
        emitted.append((event, payload))

    with (
        patch("opensquad.runner._get_session_manager", return_value=sm),
        patch("opensquad.runner.bus") as bus,
        patch("opensquad.token_breakdown.compute_token_breakdown", return_value=_breakdown()),
    ):
        bus.emit_async = AsyncMock(side_effect=_emit)
        await runner._broadcast_token_stats("sess-history")

    assert runner._turn_sid == "sess-current"
    assert emitted, "expected token_stats emit"
    _, payload = emitted[0]
    assert payload["sid"] == "sess-history"
    assert payload["data"]["used"] == len("short-history")
    assert root_api.counted_contents
    assert root_api.counted_contents[0] == ["short-history"]


@pytest.mark.asyncio
async def test_current_session_stats_prefer_live_req():
    live_req = [{"role": "user", "content": "LIVE_WINDOW"}]
    root_api = _FakeChatApi(req=live_req)
    runner = _runner(chat_api=root_api, turn_sid="sess-current")
    _bind_token_stats_methods(runner)

    sm = MagicMock()
    sm.get_focused_session_id.return_value = "sess-current"
    sm.get_current_session_id.return_value = "sess-current"
    sm.ensure_session_loaded.return_value = {
        "messages": [{"role": "user", "content": "stale-disk"}],
        "events": [],
    }

    emitted = []

    async def _emit(event, payload):
        emitted.append((event, payload))

    with (
        patch("opensquad.runner._get_session_manager", return_value=sm),
        patch("opensquad.runner.bus") as bus,
        patch("opensquad.token_breakdown.compute_token_breakdown", return_value=_breakdown()),
    ):
        bus.emit_async = AsyncMock(side_effect=_emit)
        await runner._broadcast_token_stats("sess-current")

    _, payload = emitted[0]
    assert payload["data"]["used"] == len("LIVE_WINDOW")
    assert root_api.counted_contents[0] == ["LIVE_WINDOW"]


@pytest.mark.asyncio
async def test_broadcast_includes_session_billed_usage():
    api = _FakeChatApi(req=[{"role": "user", "content": "hi"}])
    api.total_input_tokens = 1200
    api.total_output_tokens = 80
    api.total_requests = 1
    runner = _runner(chat_api=api, turn_sid="sess-1")
    _bind_token_stats_methods(runner)

    sm = MagicMock()
    sm.get_focused_session_id.return_value = "sess-1"
    sm.get_current_session_id.return_value = "sess-1"
    sm.ensure_session_loaded.return_value = {}
    emitted = []

    async def _emit(event, payload):
        emitted.append((event, payload))

    with (
        patch("opensquad.runner._get_session_manager", return_value=sm),
        patch("opensquad.runner.bus") as bus,
        patch("opensquad.token_breakdown.compute_token_breakdown", return_value=_breakdown()),
    ):
        bus.emit_async = AsyncMock(side_effect=_emit)
        await runner._broadcast_token_stats("sess-1")

    session = emitted[0][1]["data"]["session"]
    assert session["total_tokens"] == 1280
    assert session["total_requests"] == 1


@pytest.mark.asyncio
async def test_token_stats_cache_busts_after_new_turn():
    api = _FakeChatApi(req=[{"role": "system", "content": "sys"}])
    runner = _runner(chat_api=api, turn_sid="sess-1")
    _bind_token_stats_methods(runner)
    sm = MagicMock()
    sm.get_focused_session_id.return_value = "sess-1"
    sm.get_current_session_id.return_value = "sess-1"
    sm.ensure_session_loaded.return_value = {}
    emitted = []

    async def _emit(event, payload):
        emitted.append((event, payload))

    with (
        patch("opensquad.runner._get_session_manager", return_value=sm),
        patch("opensquad.runner.bus") as bus,
        patch("opensquad.token_breakdown.compute_token_breakdown", return_value=_breakdown()),
    ):
        bus.emit_async = AsyncMock(side_effect=_emit)
        await runner._broadcast_token_stats("sess-1")
        assert emitted[-1][1]["data"]["session"]["total_requests"] == 0
        api.req.append({"role": "user", "content": "hi"})
        api.total_requests = 1
        api.total_input_tokens = 400
        api.total_output_tokens = 20
        await runner._broadcast_token_stats("sess-1")

    assert emitted[-1][1]["data"]["session"]["total_requests"] == 1
    assert emitted[-1][1]["data"]["session"]["total_tokens"] == 420
