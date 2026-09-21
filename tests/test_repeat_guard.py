"""Unit tests for ``_runner/_repeat_guard.py`` — the runaway-tool-loop detector.

Regression this file exists for
------------------------------
Session ``20260921_084718_9l88`` (2026-09-21, agent305). A shell died
mid-command; the agent re-ran the same script under a fresh ``session_id`` 25
times (``chk`` → ``chk24``) and burned 48 rounds before the user hit stop.  Each
round looked like:

    system.create_shell_session {"name": "chk5"}   -> success
    system.run_session_job     {"session_id":"chk5"} -> "Command aborted (...)"

The original guard fingerprinted ``name|args_json|md5(result)`` and required a
byte-identical *call*, so the changed ``session_id`` reset the counter every
round and it never fired — even though the failure text was byte-identical 48
times.  ``failure_signature`` ignores both the arguments and the tool name so
that launder no longer works.
"""

from __future__ import annotations

from opensquad._runner import _repeat_guard as rg

ABORT_TEXT = "Command aborted: the shell process exited on its own (exit code 1) before the command finished."


def _result(name: str, args: str, text: str, failed: bool = False) -> dict:
    return {"name": name, "args_json": args, "result_text": text, "failed": failed}


def _drive(state: dict, rounds: list[list[dict]]) -> list[dict]:
    return [rg.evaluate(state, r) for r in rounds]


# ── the incident, replayed ─────────────────────────────────────────────────


def test_incident_replay_a_changing_session_id_no_longer_launders_the_loop():
    """Every round uses a fresh session id; the failure text never changes."""
    state = rg.new_state()
    decisions = []
    for i in range(1, 41):
        sid = "chk" if i == 1 else f"chk{i}"
        # The preparation round succeeds and is *different* every time.
        rg.evaluate(
            state,
            [
                _result(
                    "system.create_shell_session", f'{{"name":"{sid}"}}', f'{{"status":"success","session_id":"{sid}"}}'
                )
            ],
        )
        decisions.append(
            rg.evaluate(state, [_result("system.run_session_job", f'{{"session_id":"{sid}"}}', ABORT_TEXT, True)])
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


def test_incident_replay_messages_name_the_real_cause():
    state = rg.new_state()
    decision = None
    for i in range(rg.FAILURE_HINT_ROUNDS):
        decision = rg.evaluate(
            state, [_result("system.run_session_job", f'{{"session_id":"chk{i}"}}', ABORT_TEXT, True)]
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


def test_round_signature_still_distinguishes_arguments():
    a = rg.round_signature([_result("t", '{"x":1}', "same")])
    b = rg.round_signature([_result("t", '{"x":2}', "same")])
    assert a != b


def test_one_success_in_the_round_drops_it_from_the_failure_signature():
    both_failed = rg.failure_signature([_result("a", "{}", "boom", True), _result("b", "{}", "boom", True)])
    one_ok = rg.failure_signature([_result("a", "{}", "boom", True), _result("b", "{}", "fine")])
    assert both_failed != one_ok
