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
# sid -> epoch ms when the phase opened / ended.  Only used to tell "this
# round's phase" from a leftover of an earlier one when a thought event is
# persisted at round end (see phase_total).
_open_at_ms: dict[str, int] = {}
_last_at_ms: dict[str, int] = {}


def _wall_ms() -> int:
    return int(time.time() * 1000)


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
            _open_at_ms[key] = _wall_ms()
        ms = int((t - started) * 1000)
        payload["thought_ms"] = ms
        return ms
    if event_type in _PHASE_ENDERS:
        started = _open.pop(key, None)
        _open_at_ms.pop(key, None)
        if started is None:
            return None
        ms = int((t - started) * 1000)
        _last_ms[key] = ms
        _last_at_ms[key] = _wall_ms()
        payload["thought_ms"] = ms
        return ms
    return None


def last_ms(sid: str, scope: str = "") -> int | None:
    """Duration of the last completed thinking phase for *sid*, if any."""
    return _last_ms.get(_key(sid, scope))


def phase_total(
    sid: str,
    scope: str = "",
    *,
    since_ms: int | None = None,
) -> int | None:
    """Duration (ms) of the thinking phase a persisted ``thought`` event belongs to.

    The live UI reads the value off the wire frame; a session reloaded from disk
    only has whatever was written with the event, so the round-end persist asks
    here.  Prefer the last COMPLETED phase, but only when it ended during the
    caller's round (*since_ms*): otherwise a round that never closed its own
    phase would inherit the previous round's number, and a wrong number shown as
    fact is worse than a blank row.  A phase still open when the round ends is
    reported by its elapsed-so-far, which is the same honest figure.
    """
    key = _key(sid, scope)
    ended = _last_ms.get(key)
    if ended is not None and (since_ms is None or _last_at_ms.get(key, 0) >= since_ms):
        return ended
    started = _open.get(key)
    if started is not None and (since_ms is None or _open_at_ms.get(key, 0) >= since_ms):
        return int((time.monotonic() - started) * 1000)
    return None


def thought_event_data(
    text: str,
    sid: str,
    *,
    scope: str = "",
    since_ms: int | None = None,
) -> dict:
    """Payload for a persisted ``thought`` event: the text plus its recorded duration.

    Whoever persists a thought event must use this, or a refresh silently loses
    the duration: the persisted timestamps are ISO seconds, so the UI can only
    guess (or show nothing) once the live frame is gone.
    """
    data: dict = {"text": text}
    ms = phase_total(sid, scope, since_ms=since_ms)
    if ms is not None:
        data["thought_ms"] = ms
    return data


def reset(sid: str | None = None) -> None:
    """Drop the phase state for one session (or all of them)."""
    if sid is None:
        _open.clear()
        _last_ms.clear()
        _open_at_ms.clear()
        _last_at_ms.clear()
        return
    for store in (_open, _last_ms, _open_at_ms, _last_at_ms):
        for key in [k for k in store if k.split("\0", 1)[0] == (sid or "")]:
            store.pop(key, None)
