"""The agent records how long a thinking phase took; the UI no longer guesses.

Why this exists
---------------
The Web UI inferred each 深度思考 row's duration from the timestamps of the
neighbouring workflow events.  That guess drifts (live frames carry ms, the
persisted events only ISO seconds, so a refreshed session showed no duration at
all) and every new event ordering — batch merges, flush rewrites, sub-agent
frames, block sealing — produced a new visible symptom.  Reported repeatedly as
"深度思考计时还在算" and "计时不准".

What is pinned here is the recorder, not the guess:
  * every ``thought`` frame carries the elapsed time so far (``thought_ms``);
  * the frame that ENDS the phase carries the exact total and closes the phase;
  * a heartbeat-style frame must NOT close a phase (they fire on their own
    schedule — ``busy_sessions`` re-broadcasts every few seconds);
  * sub-agents stream under the PARENT's session id, so their phases must not
    open or close the parent's;
  * the adapter promotes the value onto the wire frame, and the SDK's frame
    whitelist keeps it.

Mutations verified (applied, run, reverted):
  M1 add a heartbeat type (``status``) to the phase enders   -> R2b fails
  M2 make the clock global instead of per session            -> R3 fails
  M3 keep the phase open after stamping the end              -> R4 fails
  M4 drop ``thought_ms`` from the SDK frame whitelist         -> R6b fails
  M5 stop stamping the frame in the adapter                  -> R6 fails
  M6 stop persisting it with the thought event               -> the recorder test
      in ``test_turn_result_handler.py`` fails
"""

from __future__ import annotations

import pytest

from opensquad import thought_clock


@pytest.fixture(autouse=True)
def _clean_clock():
    thought_clock.reset()
    yield
    thought_clock.reset()


def test_thought_frames_carry_the_growing_elapsed():
    """R1 — each chunk reports the phase elapsed so far."""
    payload: dict = {}
    assert thought_clock.stamp("thought", "s1", payload, now=100.0) == 0
    assert payload["thought_ms"] == 0
    payload = {}
    assert thought_clock.stamp("thought", "s1", payload, now=102.4) == 2400
    assert payload["thought_ms"] == 2400


def test_the_phase_ender_carries_the_total_and_closes_the_phase():
    """R2 — the tool_call freezes the row with the exact duration."""
    thought_clock.stamp("thought", "s1", {}, now=100.0)
    end: dict = {}
    assert thought_clock.stamp("tool_call", "s1", end, now=105.5) == 5500
    assert end["thought_ms"] == 5500
    assert thought_clock.last_ms("s1") == 5500
    # A second ender without a new phase reports nothing (idempotent for the UI).
    assert thought_clock.stamp("tool_result", "s1", {}) is None


def test_heartbeat_frames_do_not_cut_a_phase_short():
    """R2b — status/busy frames fire on their own schedule, mid-think."""
    thought_clock.stamp("thought", "s1", {}, now=100.0)
    for heartbeat in ("status", "busy_sessions", "token_stats", "state", "wake"):
        assert thought_clock.stamp(heartbeat, "s1", {}, now=101.0) is None
    end: dict = {}
    assert thought_clock.stamp("tool_call", "s1", end, now=106.0) == 6000


def test_parallel_sessions_keep_their_own_clock():
    """R3 — two panes thinking at once must not share a phase."""
    thought_clock.stamp("thought", "A", {}, now=100.0)
    thought_clock.stamp("thought", "B", {}, now=103.0)
    a: dict = {}
    assert thought_clock.stamp("tool_call", "A", a, now=105.0) == 5000
    b: dict = {}
    assert thought_clock.stamp("tool_call", "B", b, now=110.0) == 7000


def test_a_new_phase_starts_after_the_previous_one_ends():
    """R4 — the clock resets between rounds."""
    thought_clock.stamp("thought", "s1", {}, now=100.0)
    thought_clock.stamp("tool_call", "s1", {}, now=104.0)
    thought_clock.stamp("thought", "s1", {}, now=120.0)
    second: dict = {}
    assert thought_clock.stamp("message", "s1", second, now=121.5) == 1500


def test_sub_agent_phases_do_not_touch_the_parent():
    """R5 — delegate reasoning streams under the parent sid, tagged sub_agent."""
    thought_clock.stamp("thought", "s1", {}, now=100.0)  # parent
    thought_clock.stamp("thought", "s1", {}, scope="sub", now=140.0)  # delegate
    delegate: dict = {}
    assert thought_clock.stamp("tool_call", "s1", delegate, scope="sub", now=142.0) == 2000
    # The parent's own phase is untouched and still open — it ends on its own frame.
    parent: dict = {}
    assert thought_clock.stamp("tool_call", "s1", parent, now=150.0) == 50000


def test_adapter_promotes_the_value_onto_the_frame():
    """R6 — the wire frame carries it, scoped away from sub-agent frames."""
    from opensquad.gateway_adapter import GatewayAdapter

    adapter = GatewayAdapter.__new__(GatewayAdapter)

    sent: list[tuple] = []

    async def _fake_send_response_to_user(uid, content, msg_type="message", sid="", **meta):
        sent.append((msg_type, sid, meta))

    async def _fake_send_response(content, msg_type="message", sid="", **meta):
        sent.append((msg_type, sid, meta))

    adapter._user_id_by_sid = {"s1": "u1"}
    adapter.current_user_id = None
    adapter.send_response_to_user = _fake_send_response_to_user
    adapter.send_response = _fake_send_response

    import asyncio

    asyncio.run(adapter._send_event("reasoning", "thought", sid="s1"))
    asyncio.run(adapter._send_event({"id": "c1"}, "tool_call", sid="s1"))
    # A frame with no open phase must not carry a stale value.
    asyncio.run(adapter._send_event("later", "status", sid="s1"))

    kinds = [(t, m.get("thought_ms")) for t, _sid, m in sent]
    assert kinds[0][1] is not None, "thought frame must report elapsed"
    assert kinds[1][1] is not None, "the tool_call must freeze the phase"
    assert kinds[2][1] is None, "a heartbeat frame must not invent a duration"


def test_sdk_frame_whitelist_keeps_thought_ms():
    """R6b — otherwise the field is dropped on the way to the browser."""
    from opensquad import sdk

    assert "thought_ms" in sdk._FRAME_META_KEYS
