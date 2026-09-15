"""Regression locks: per-round token usage events (``turn_usage``).

Feature (2026-09-13 user request): the agent message footer must show this
round's total token cost (消耗 badge) with a hover/click popover for the
input/output split — like "9m 34s · 3.0M" in reference products.

Wiring locked here:
- ``AgentRunner._round_usage_snapshot`` reads the ChatAPI bound to the session.
- ``AgentRunner._finalize_round_usage`` diffs the snapshot at round teardown,
  persists a ``turn_usage`` session event AND broadcasts it over the bus.
- Both the parallel (``_parallel_session_turn``) and serial (``_run_serial``)
  round loops snapshot at round start and finalize at every teardown exit
  (normal / user-stop / cancelled / failed).
- The gateway adapter forwards ``turn_usage`` to web clients.
"""

from __future__ import annotations

from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1] / "src" / "opensquad"
RUNNER_SRC = (APP_ROOT / "runner.py").read_text(encoding="utf-8")
ADAPTER_SRC = (APP_ROOT / "gateway_adapter.py").read_text(encoding="utf-8")


class _FakeApi:
    def __init__(self, inp: int = 0, out: int = 0):
        self.total_input_tokens = inp
        self.total_output_tokens = out


class _FakeSessionManager:
    def __init__(self):
        self.events: list[dict] = []

    def add_event(self, event_type, event_data, turn_id=None, round_id=None, *, sid=None):
        self.events.append(
            {
                "type": event_type,
                "data": event_data,
                "turn_id": turn_id,
                "round_id": round_id,
                "sid": sid,
            }
        )


def _bare_runner(api: _FakeApi | None = None, session_apis: dict | None = None):
    from opensquad.runner import AgentRunner
    from opensquad.session_parallel import TurnLocal

    r = object.__new__(AgentRunner)
    r._root_tl = TurnLocal()  # chat_api is a property over this
    r.chat_api = api if api is not None else _FakeApi()
    if session_apis is not None:
        r._session_chat_apis = session_apis
    r._current_round = 3
    r._current_turn = 7
    r._workflow_started_ms = 1_000
    return r


# --------------------------------------------------------------------------
# Behavioural: snapshot + finalize
# --------------------------------------------------------------------------


def test_snapshot_prefers_session_bound_api():
    session_api = _FakeApi(11, 22)
    root_api = _FakeApi(999, 999)
    r = _bare_runner(api=root_api, session_apis={"sid-a": session_api})
    snap = r._round_usage_snapshot("sid-a")
    assert snap["input"] == 11
    assert snap["output"] == 22
    assert snap["api"] is session_api


def test_snapshot_falls_back_to_root_api():
    root_api = _FakeApi(5, 6)
    r = _bare_runner(api=root_api)
    snap = r._round_usage_snapshot("")
    assert snap["api"] is root_api
    assert snap["input"] == 5 and snap["output"] == 6


@pytest.mark.asyncio
async def test_finalize_emits_delta_over_start(monkeypatch):
    import opensquad.runner as runner_mod

    api = _FakeApi(100, 50)
    r = _bare_runner(api=api)
    start = r._round_usage_snapshot("sid-a")
    api.total_input_tokens = 200
    api.total_output_tokens = 120

    sm = _FakeSessionManager()
    monkeypatch.setattr(runner_mod, "_get_session_manager", lambda: sm)
    emitted: list[tuple[str, dict, str | None]] = []

    async def fake_emit(etype, data, *, sid=None):
        emitted.append((etype, data, sid))

    r._emit = fake_emit  # instance override: no real bus fan-out in tests

    await r._finalize_round_usage("sid-a", start, started_ms=1_000)

    assert len(sm.events) == 1, "usage must be persisted for refresh rebuilds"
    evt = sm.events[0]
    assert evt["type"] == "turn_usage"
    assert evt["sid"] == "sid-a"
    assert evt["turn_id"] == 7 and evt["round_id"] == 3
    data = evt["data"]
    assert data["input_tokens"] == 100, "200-100 billed this round"
    assert data["output_tokens"] == 70, "120-50 billed this round"
    assert data["total_tokens"] == 170
    assert data["elapsed_ms"] == data["ended_ms"] - data["started_ms"]
    assert emitted and emitted[0][0] == "turn_usage"
    assert emitted[0][1] == data, "WS payload must match the persisted one"
    assert emitted[0][2] == "sid-a"


@pytest.mark.asyncio
async def test_finalize_after_api_rebind_uses_absolute_totals(monkeypatch):
    import opensquad.runner as runner_mod

    old_api = _FakeApi(500, 200)
    r = _bare_runner(api=old_api)
    start = r._round_usage_snapshot("sid-a")

    # Auth fallback rebinds the ChatAPI mid-round: old counters unreachable.
    new_api = _FakeApi(80, 40)
    r._session_chat_apis = {"sid-a": new_api}

    sm = _FakeSessionManager()
    monkeypatch.setattr(runner_mod, "_get_session_manager", lambda: sm)

    async def fake_emit(etype, data, *, sid=None):
        pass

    r._emit = fake_emit
    await r._finalize_round_usage("sid-a", start, started_ms=1_000)

    data = sm.events[0]["data"]
    assert data["input_tokens"] == 80 and data["output_tokens"] == 40


@pytest.mark.asyncio
async def test_finalize_skips_zero_usage_rounds(monkeypatch):
    import opensquad.runner as runner_mod

    api = _FakeApi(10, 10)
    r = _bare_runner(api=api)
    start = r._round_usage_snapshot("sid-a")  # nothing billed afterwards

    sm = _FakeSessionManager()
    monkeypatch.setattr(runner_mod, "_get_session_manager", lambda: sm)

    async def fake_emit(etype, data, *, sid=None):
        raise AssertionError("no WS event expected for a zero-usage round")

    r._emit = fake_emit
    await r._finalize_round_usage("sid-a", start, started_ms=1_000)
    assert sm.events == []


@pytest.mark.asyncio
async def test_finalize_clamps_negative_delta_on_counter_reset(monkeypatch):
    """A session switch / stats reset can shrink the counters mid-round: the
    delta goes negative and must be clamped to 0, never emitted as negative."""
    import opensquad.runner as runner_mod

    api = _FakeApi(500, 300)
    r = _bare_runner(api=api)
    start = r._round_usage_snapshot("sid-a")
    api.total_input_tokens = 100  # counter shrank below the baseline
    api.total_output_tokens = 350  # output still grew

    sm = _FakeSessionManager()
    monkeypatch.setattr(runner_mod, "_get_session_manager", lambda: sm)

    async def fake_emit(etype, data, *, sid=None):
        pass

    r._emit = fake_emit
    await r._finalize_round_usage("sid-a", start, started_ms=1_000)

    data = sm.events[0]["data"]
    assert data["input_tokens"] == 0, "negative delta must clamp to 0"
    assert data["output_tokens"] == 50
    assert data["total_tokens"] == 50


@pytest.mark.asyncio
async def test_finalize_never_raises_on_manager_failure(monkeypatch):
    import opensquad.runner as runner_mod

    api = _FakeApi(10, 10)
    r = _bare_runner(api=api)
    start = r._round_usage_snapshot("sid-a")
    api.total_input_tokens = 30

    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(runner_mod, "_get_session_manager", boom)

    async def fake_emit(etype, data, *, sid=None):
        pass

    r._emit = fake_emit
    await r._finalize_round_usage("sid-a", start, started_ms=1_000)  # must not raise


# --------------------------------------------------------------------------
# Source-level locks: the wiring in both round loops + gateway forwarding
# --------------------------------------------------------------------------


def test_parallel_loop_snapshots_at_round_start_and_finalizes_all_exits():
    block = RUNNER_SRC[RUNNER_SRC.index("async def _parallel_session_turn") :]
    block = block[: block.index("async def _run_serial")]
    # Snapshot right after the workflow clock starts, not at function entry —
    # an earlier snapshot would swallow nothing, a later one would miss calls.
    assert "_round_usage_start = self._round_usage_snapshot(sid)" in block
    assert block.index("_workflow_started_ms = datetime.now()") < block.index(
        "_round_usage_start = self._round_usage_snapshot(sid)"
    )
    # Three teardown exits: normal end, CancelledError, generic failure.
    assert block.count("await self._finalize_round_usage(") == 3
    # The bare name must be initialised before the main try so the except
    # blocks can reference it even when the failure precedes the snapshot.
    init_at = block.index("_round_usage_start: dict | None = None")
    main_try_at = block.index("try:", block.index("token = set_turn_local(tl)"))
    assert init_at < main_try_at


def test_serial_loop_snapshots_and_finalizes_both_teardowns():
    block = RUNNER_SRC[RUNNER_SRC.index("async def _run_serial") :]
    block = block[: block.index("def _generate_wake_prompt")]
    assert '_round_usage_start = self._round_usage_snapshot(self._turn_sid or "")' in block
    assert block.count("await self._finalize_round_usage(") == 2
    # Both the user-stop exit and the task_finished exit are covered.
    stop_at = block.index("if was_user_stop:")
    done_at = block.index("if task_finished:")
    final_uses = [m for m in range(len(block)) if block.startswith("await self._finalize_round_usage", m)]
    # Exit 1: user-stop branch (finalize lands between `turn_elapsed` and
    # `if was_user_stop:`). Exit 2: inside the `if task_finished:` seal.
    assert any(m < stop_at for m in final_uses)
    assert any(m > done_at for m in final_uses)


def test_finalize_persists_and_broadcasts_turn_usage():
    fn_src = RUNNER_SRC[RUNNER_SRC.index("async def _finalize_round_usage") :]
    fn_src = fn_src[: fn_src.index("def _chat_api_owns_session")]
    assert '"turn_usage"' in fn_src
    assert "add_event(" in fn_src, "usage must be persisted for history rebuilds"
    assert 'self._emit("turn_usage"' in fn_src, "usage must be broadcast for live update"
    for field in ("input_tokens", "output_tokens", "total_tokens", "elapsed_ms", "started_ms", "ended_ms"):
        assert field in fn_src


def test_gateway_adapter_forwards_turn_usage():
    assert '_sub("turn_usage", self.on_generic_event("turn_usage"))' in ADAPTER_SRC


def test_payload_never_includes_negative_deltas():
    fn_src = RUNNER_SRC[RUNNER_SRC.index("async def _finalize_round_usage") :]
    fn_src = fn_src[: fn_src.index("def _chat_api_owns_session")]
    # Both the input and the output delta lines must clamp independently.
    assert fn_src.count("max(0,") >= 2, "input AND output deltas need the zero clamp"
