"""Unit tests for ``_runner/_repeat_guard.py`` — the runaway-tool-loop detector.

Regression this file exists for
------------------------------
Session ``20260921_084718_9l88`` (2026-09-21, agent305). A shell died
mid-command; the agent re-ran the same script under a fresh ``session_id`` 25
times (``chk`` → ``chk24``) and burned 48 rounds before the user hit stop.  Each
round looked like:

    system.create_shell_session {"name": "chk5"}     -> success
    system.run_session_job     {"session_id":"chk5"} -> "Command aborted (...)"

The original guard fingerprinted ``name|args_json|md5(result)`` and required a
byte-identical *call*, so the changed ``session_id`` reset the counter every
round and it never fired.

Why the replay below builds *real* payloads
-------------------------------------------
An earlier version of this file fed a hand-written constant string and asserted
"the failure text never changes".  That was the same wrong assumption the first
fix made: the text handed to the model is deliberately verbose and names the
session the retry just minted, so digesting it reset the counter on every round
and the signal was dead on exactly this incident (40 rounds, ``fail_count`` stuck
at 1).  The replay therefore drives the abort dict from ``tools/system.py``
through the real ``format_result_for_llm`` / ``failure_key`` — the same assembly
``_turn_loop`` uses.  Keep the payload real.
"""

from __future__ import annotations

from opensquad._runner import _repeat_guard as rg
from opensquad._runner._result_formatter import failure_key, format_result_for_llm, is_failure_result

CWD = r"c:\users\adminuser\desktop\战略\ai\skill"


def _abort_result(session_id: str, exit_code: int = 1) -> dict:
    """The ``shell_exited`` abort dict, byte-for-byte as ``tools/system.py`` returns it."""
    return {
        "status": "error",
        "session_id": session_id,
        "reason": "shell_exited",
        "return_code": exit_code,
        "message": (
            f"Command aborted: the shell process exited on its own (exit code {exit_code}) before the command finished."
        ),
        "partial_data": "",
        "working_directory": CWD,
        "aborted": True,
        "hint": "Anything the command printed before the shell died is in partial_data.",
    }


def _prep_result(session_id: str) -> dict:
    return {
        "status": "success",
        "session_id": session_id,
        "message": f"Shell session '{session_id}' created.",
    }


def _entry(name: str, args_json: str, result) -> dict:
    """Assemble a guard input exactly like ``_turn_loop`` does."""
    return {
        "name": name,
        "args_json": args_json,
        "result_text": format_result_for_llm(result),
        "failed": is_failure_result(result),
        "failure_key": failure_key(result),
    }


def _result(name: str, args: str, text: str, failed: bool = False, key: str | None = None) -> dict:
    return {
        "name": name,
        "args_json": args,
        "result_text": text,
        "failed": failed,
        "failure_key": text if key is None else key,
    }


def _drive(state: dict, rounds: list[list[dict]]) -> list[dict]:
    return [rg.evaluate(state, r) for r in rounds]


# ── the incident, replayed ─────────────────────────────────────────────────


def test_incident_replay_a_changing_session_id_no_longer_launders_the_loop():
    """Every round uses a fresh session id; the guard must still fire."""
    state = rg.new_state()
    decisions = []
    for i in range(1, 41):
        sid = "chk" if i == 1 else f"chk{i}"
        # The preparation round succeeds and is *different* every time.
        rg.evaluate(
            state,
            [_entry("system.create_shell_session", f'{{"name":"{sid}"}}', _prep_result(sid))],
        )
        decisions.append(
            rg.evaluate(
                state,
                [_entry("system.run_session_job", f'{{"session_id":"{sid}"}}', _abort_result(sid))],
            )
        )

    hints = [i for i, d in enumerate(decisions, 1) if d["action"] == rg.ACTION_HINT]
    aborts = [i for i, d in enumerate(decisions, 1) if d["action"] == rg.ACTION_ABORT]

    assert hints == [rg.FAILURE_HINT_ROUNDS], f"expected one hint on failure #{rg.FAILURE_HINT_ROUNDS}, got {hints}"
    assert aborts and aborts[0] == rg.FAILURE_ABORT_ROUNDS, (
        f"expected abort on failure #{rg.FAILURE_ABORT_ROUNDS}, got {aborts}"
    )
    assert all(d["signal"] in (None, rg.SIGNAL_FAILURE) for d in decisions)
    # Proof the new signal is what caught it: the strict counter never got close.
    assert state["count"] < rg.STRICT_HINT_ROUNDS, "the strict signal must not be the one that fired"
    assert state["fail_count"] >= rg.FAILURE_ABORT_ROUNDS


def test_the_failure_signal_does_not_digest_the_rendered_text():
    """The rendered text names the session, so it differs on every retry.

    Digesting it is *precisely* the bug that left this guard dead on the
    incident: the two abort payloads below are the same complaint, and the guard
    has to agree — even though what the model read is not byte-identical.
    """
    a = _entry("system.run_session_job", '{"session_id":"chk5"}', _abort_result("chk5"))
    b = _entry("system.run_session_job", '{"session_id":"chk6"}', _abort_result("chk6"))

    assert a["result_text"] != b["result_text"], "premise: the rendered text differs per attempt"
    assert a["failure_key"] == b["failure_key"]
    assert rg.failure_signature([a]) == rg.failure_signature([b])


def test_incident_replay_messages_name_the_real_cause():
    state = rg.new_state()
    decision = None
    for i in range(rg.FAILURE_HINT_ROUNDS):
        decision = rg.evaluate(
            state,
            [_entry("system.run_session_job", f'{{"session_id":"chk{i}"}}', _abort_result(f"chk{i}"))],
        )

    hint = rg.hint_message(decision)
    assert "完全相同的失败结果" in hint
    assert "system.run_session_job" in hint
    assert str(rg.FAILURE_HINT_ROUNDS) in hint

    abort = rg.abort_message(decision)
    assert "无进展循环" in abort
    assert "system.run_session_job" in abort


# ── the original strict signal still works ─────────────────────────────────


def test_identical_call_and_result_still_hints_then_aborts():
    state = rg.new_state()
    rounds = [
        [_result("filesystem.read_file", '{"path":"a.txt"}', "same bytes")] for _ in range(rg.STRICT_ABORT_ROUNDS + 1)
    ]
    decisions = _drive(state, rounds)

    assert decisions[rg.STRICT_HINT_ROUNDS - 1]["action"] == rg.ACTION_HINT
    assert decisions[rg.STRICT_HINT_ROUNDS - 1]["signal"] == rg.SIGNAL_STRICT
    assert decisions[rg.STRICT_ABORT_ROUNDS - 1]["action"] == rg.ACTION_ABORT
    assert [d["action"] for d in decisions].count(rg.ACTION_HINT) == 1


def test_changing_successful_results_are_never_flagged():
    """Legitimate polling: same call, different output every round."""
    state = rg.new_state()
    for i in range(40):
        decision = rg.evaluate(state, [_result("system.check_job", '{"job_id":"j1"}', f"line {i}")])
        assert decision["action"] == rg.ACTION_NONE
    assert state["fail_count"] == 0


def test_a_still_running_poll_is_not_a_failure():
    """`completed=False` is progress-in-waiting, not an error — see is_failure_result."""
    state = rg.new_state()
    for _ in range(20):
        rg.evaluate(state, [_result("system.start_job", '{"cmd":"build"}', "still running", failed=False)])
    assert state["fail_count"] == 0


# ── streak resets ──────────────────────────────────────────────────────────


def test_a_different_failure_resets_the_streak():
    state = rg.new_state()
    for _ in range(rg.FAILURE_HINT_ROUNDS - 1):
        rg.evaluate(state, [_result("t", "{}", "boom A", True)])
    assert state["fail_count"] == rg.FAILURE_HINT_ROUNDS - 1

    decision = rg.evaluate(state, [_result("t", "{}", "boom B", True)])

    assert decision["action"] == rg.ACTION_NONE
    assert state["fail_count"] == 1


def test_an_old_failure_is_not_blamed_on_a_new_one():
    """The same text after a long quiet stretch is a new problem, not a loop."""
    state = rg.new_state()
    rg.evaluate(state, [_result("t", "{}", "boom", True)])
    for _ in range(rg.FAILURE_STALENESS_ROUNDS + 1):
        rg.evaluate(state, [_result("t", "{}", "fine")])
    assert state["fail_idle"] > rg.FAILURE_STALENESS_ROUNDS

    rg.evaluate(state, [_result("t", "{}", "boom", True)])

    assert state["fail_count"] == 1


def test_a_round_without_results_is_a_noop():
    state = rg.new_state()
    before = dict(state)
    assert rg.evaluate(state, [])["action"] == rg.ACTION_NONE
    assert state == before


def test_a_session_that_recovers_loses_its_guard_flag():
    """Once the call changes, the hint may fire again on a *new* streak."""
    state = rg.new_state()
    for _ in range(rg.STRICT_HINT_ROUNDS):
        rg.evaluate(state, [_result("t", '{"a":1}', "same")])
    assert state["guarded"] is True

    rg.evaluate(state, [_result("t", '{"a":2}', "same")])

    assert state["guarded"] is False
    assert state["count"] == 1


# ── signatures ─────────────────────────────────────────────────────────────


def test_failure_signature_ignores_tool_name_and_arguments():
    a = rg.failure_signature([_result("tool_a", '{"x":1}', "same failure", True)])
    b = rg.failure_signature([_result("tool_b", '{"x":2}', "same failure", True)])
    assert a is not None and a == b


def test_failure_signature_is_none_without_a_failure():
    assert rg.failure_signature([]) is None
    assert rg.failure_signature([_result("t", "{}", "ok")]) is None


def test_a_missing_failure_key_falls_back_to_the_text():
    """Older call sites only pass text; that path must still yield a signature."""
    assert rg.failure_signature([_result("t", "{}", "boom", True, key="")]) is not None


def test_round_signature_still_distinguishes_arguments():
    a = rg.round_signature([_result("t", '{"x":1}', "same")])
    b = rg.round_signature([_result("t", '{"x":2}', "same")])
    assert a != b


def test_one_success_in_the_round_drops_it_from_the_failure_signature():
    both_failed = rg.failure_signature([_result("a", "{}", "boom", True), _result("b", "{}", "boom", True)])
    one_ok = rg.failure_signature([_result("a", "{}", "boom", True), _result("b", "{}", "fine")])
    assert both_failed != one_ok
