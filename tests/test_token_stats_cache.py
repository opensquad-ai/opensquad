"""Tests for _broadcast_token_stats TTL caching (P0-2 web latency fix).

The full-history tiktoken re-encoding is expensive (200-800ms on long
sessions) and runs on the agent event loop; the frontend polls every 12s.
These tests assert recomputes collapse to one per TTL while every call still
broadcasts the (cached) payload.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import opensquad.runner as runner_module


def _make_runner():
    runner = MagicMock()
    runner._agent_id = "test-agent"
    runner._turn_sid = "sess-1"
    runner._resolve_token_stats_sid = lambda: "sess-1"
    runner._chat_api_for_token_stats = lambda sid: MagicMock(
        req=[],
        total_input_tokens=0,
        total_output_tokens=0,
        total_requests=0,
        total_cache_read_tokens=0,
        token_max=8000,
        history_dir=None,
        _count_tokens=lambda req, tools: 10,
        encoding=None,
    )
    runner._tools_for_token_stats = lambda: []
    runner._req_for_token_stats = lambda chat_api, sid: []
    runner._hist_input_tokens = 0
    runner._hist_output_tokens = 0
    runner._hist_requests = 0
    runner._hist_cache_read_tokens = 0
    runner._hist_cache_creation_tokens = 0
    runner._token_stats_cache = {}
    return runner


@pytest.fixture
def runner(monkeypatch):
    r = _make_runner()
    # Count real recomputes by wrapping compute_token_breakdown
    calls = {"n": 0}
    runner_module.token_breakdown.compute_token_breakdown if hasattr(runner_module, "token_breakdown") else None
    import opensquad.token_breakdown as tb

    def fake_compute(*a, **kw):
        calls["n"] += 1
        return {"system": 0, "user": 0, "thought": 0, "tool": 0, "tool_defs": 0, "response": 0, "overhead": 0}

    monkeypatch.setattr(tb, "compute_token_breakdown", fake_compute)
    r._calls = calls
    return r


async def test_first_call_recomputes(runner):
    await runner_module.AgentRunner._broadcast_token_stats(runner, None)
    assert runner._calls["n"] == 1


async def test_second_call_within_ttl_uses_cache(runner, monkeypatch):
    await runner_module.AgentRunner._broadcast_token_stats(runner, None)
    await runner_module.AgentRunner._broadcast_token_stats(runner, None)
    assert runner._calls["n"] == 1  # recomputed once, cached second time


async def test_cached_call_still_broadcasts(runner):
    emitted = []

    async def spy_emit(event_type, data):
        emitted.append((event_type, data))

    import opensquad.events as ev

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ev.bus, "emit_async", spy_emit)
    try:
        await runner_module.AgentRunner._broadcast_token_stats(runner, None)
        await runner_module.AgentRunner._broadcast_token_stats(runner, None)
    finally:
        monkeypatch.undo()
    token_events = [e for e in emitted if e[0] == "token_stats"]
    assert len(token_events) == 2


async def test_ttl_expiry_recomputes(runner):
    await runner_module.AgentRunner._broadcast_token_stats(runner, None)
    # Force expiry
    key = "sess-1"
    cached = runner._token_stats_cache[key]
    runner._token_stats_cache[key] = (cached[0] - 100, cached[1])
    await runner_module.AgentRunner._broadcast_token_stats(runner, None)
    assert runner._calls["n"] == 2
