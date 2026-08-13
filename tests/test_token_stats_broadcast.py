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

    def _count_tokens(self, messages, tools=None):
        return 42


@pytest.mark.asyncio
async def test_broadcast_token_stats_falls_back_sid_and_emits():
    from opensquad.runner import AgentRunner

    root_api = _FakeChatApi()
    session_api = _FakeChatApi(token_max=200000, req=[{"role": "user", "content": "hi"}])

    runner = SimpleNamespace(
        _turn_sid="",
        _fallback_turn_sid="",
        _agent_id="agent-a",
        _session_chat_apis={"sess-1": session_api},
        chat_api=root_api,
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
    # Bind real methods
    runner._resolve_token_stats_sid = AgentRunner._resolve_token_stats_sid.__get__(runner, type(runner))
    runner._chat_api_for_token_stats = AgentRunner._chat_api_for_token_stats.__get__(runner, type(runner))
    runner._req_for_token_stats = AgentRunner._req_for_token_stats.__get__(runner, type(runner))
    runner._broadcast_token_stats = AgentRunner._broadcast_token_stats.__get__(runner, type(runner))

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
    assert payload["data"]["used"] == 42
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
