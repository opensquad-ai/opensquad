"""Deep-think phase clock — record the thinking duration instead of inferring it.

The Agent Web used to *infer* a thought row's duration from the timestamps of the
events around it.  That guess drifts (live frames carry milliseconds, the
persisted events only ISO seconds) and every new event ordering — batch merges,
flush rewrites, sub-agent frames, block sealing — produced a new visible symptom
("深度思考 Ns 还在算", a duration that never freezes, or no duration at all).

So record it where it is actually known: at the emitter.  Every ``thought`` frame
carries the phase's elapsed time so far, and the frame that ends the phase (the
tool call, or the answer that follows the reasoning) carries the exact total.
The UI shows that number verbatim and never has to guess.

Per ``sid``: one agent process can drive several sessions in parallel, and a
global clock would mix their phases.
"""

from __future__ import annotations

import time

# Frames that END a thinking phase: the reasoning is over for this round.  A
# status/heartbeat frame (``busy_sessions``, ``status``, ``token_stats``, …) must
# NOT close a phase — those fire on their own schedule (the dispatcher re-broadcasts
# busy_sessions every few seconds) and would cut every long think short.
_PHASE_ENDERS = frozenset(
    {
        "tool_call",
        "tool_call_delta",
        "tool_result",
        "stream",
        "to_user_stream",
        "message",
        "response",
        "to_user_final",
        "to_user_reply",
        "to_user_end_task",
        "plan",
    }
)

# sid -> monotonic start of its open thinking phase (absent = no phase open)
_open: dict[str, float] = {}
# sid -> duration of its last completed phase, in ms
_last_ms: dict[str, int] = {}


def _key(sid: str, scope: str) -> str:
    # Sub-agents stream under the PARENT's session id (tagged ``sub_agent``), so
    # the scope must be part of the key or a delegate's reasoning would open and
    # close the parent's phase.
    return f"{sid or ''}\0{scope or ''}"


def stamp(
    event_type: str,
    sid: str,
    payload: dict,
    *,
    scope: str = "",
    now: float | None = None,
) -> int | None:
    """Annotate an outbound event payload with the thinking-phase duration (ms).

    Mutates *payload* in place (the emitters hand over the dict they are about to
    publish) and returns the value it attached, if any.
    """
    if not isinstance(payload, dict):
        return None
    key = _key(sid, scope)
    t = time.monotonic() if now is None else now
    if event_type == "thought":
        started = _open.get(key)
        if started is None:
            started = t
            _open[key] = started
        ms = int((t - started) * 1000)
        payload["thought_ms"] = ms
        return ms
    if event_type in _PHASE_ENDERS:
        started = _open.pop(key, None)
        if started is None:
            return None
        ms = int((t - started) * 1000)
        _last_ms[key] = ms
        payload["thought_ms"] = ms
        return ms
    return None


def last_ms(sid: str, scope: str = "") -> int | None:
    """Duration of the last completed thinking phase for *sid*, if any."""
    return _last_ms.get(_key(sid, scope))


def reset(sid: str | None = None) -> None:
    """Drop the phase state for one session (or all of them)."""
    if sid is None:
        _open.clear()
        _last_ms.clear()
        return
    for store in (_open, _last_ms):
        for key in [k for k in store if k.split("\0", 1)[0] == (sid or "")]:
            store.pop(key, None)
