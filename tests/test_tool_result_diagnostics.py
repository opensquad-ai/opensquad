"""The text a tool result hands the model must keep its diagnostics.

Regression this file exists for
------------------------------
Session ``20260921_084718_9l88`` (2026-09-21). ``_turn_loop`` collapsed every
result dict that carried a ``message`` key down to that one string, so a shell
that died mid-command told the model::

    Command aborted (shell closed or process exited)

and threw away ``partial_data`` (the output produced before the shell died),
``return_code``, ``working_directory`` and ``reason``.  The model, unable to tell
"retry" from "switch strategy", re-ran the same command under 25 fresh session
ids and burned 48 rounds.

The same file also locked the ``system.py`` side: "the user pressed stop" and
"the shell process exited on its own" used to be indistinguishable, and the
message that ended up in front of the model was identical for both.

A third concern lives here too: ``failure_key``.  Making the result verbose
(above) is what killed the repeated-action guard's failure signal — the verbose
text names the session the retry just minted, so digesting it reset the counter
every round.  The guard digests ``failure_key`` instead: our own taxonomy
(``reason``/``status`` + digit-masked message), stable across attempts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from opensquad._runner._result_formatter import (
    _RESULT_DETAIL_KEYS,
    failure_key,
    format_result_for_llm,
    is_failure_result,
)

APP_ROOT = Path(__file__).resolve().parents[1] / "src" / "opensquad"
TURN_LOOP_SRC = (APP_ROOT / "_runner" / "_turn_loop.py").read_text(encoding="utf-8")
SYSTEM_SRC = (APP_ROOT / "tools" / "system.py").read_text(encoding="utf-8")

ABORTED_SHELL_RESULT = {
    "status": "error",
    "session_id": "chk24",
    "reason": "shell_exited",
    "return_code": 1,
    "message": "Command aborted: the shell process exited on its own (exit code 1) before the command finished.",
    "partial_data": "txt_ok=0 txt_garbled=0\nTraceback (most recent call last): ...",
    "working_directory": r"c:\users\adminuser\desktop\战略\ai\skill",
    "aborted": True,
    "hint": "Anything the command printed before the shell died is in partial_data.",
}


# ── format_result_for_llm ──────────────────────────────────────────────────


def test_aborted_shell_result_keeps_its_diagnostics():
    text = format_result_for_llm(ABORTED_SHELL_RESULT)

    assert text.startswith(ABORTED_SHELL_RESULT["message"])
    for fragment in (
        "txt_ok=0 txt_garbled=0",
        "[reason] shell_exited",
        "[session_id] chk24",
        "[return_code] 1",
        "[partial_data]",
        "[working_directory]",
        "[hint]",
    ):
        assert fragment in text, f"lost {fragment!r}"


def test_the_trailing_hint_survives_truncation():
    """`hint` is last so `truncate_result_text`'s tail window keeps it."""
    from opensquad._runner._result_formatter import truncate_result_text

    text = format_result_for_llm({**ABORTED_SHELL_RESULT, "partial_data": "x" * 200_000})
    truncated = truncate_result_text(text, 50_000)

    assert "[hint]" in truncated
    assert ABORTED_SHELL_RESULT["message"] in truncated


def test_empty_partial_data_is_stated_not_silently_dropped():
    text = format_result_for_llm({"status": "error", "message": "boom", "partial_data": ""})
    assert "[partial_data] (empty)" in text


def test_a_message_only_result_is_unchanged():
    assert format_result_for_llm({"status": "success", "message": "File written"}) == "File written"


def test_results_without_a_message_keep_the_historical_rendering():
    assert format_result_for_llm("raw text") == "raw text"
    assert format_result_for_llm("") == "(empty result)"
    assert format_result_for_llm(None) == "(empty result)"
    payload = {"status": "success", "data": "x"}
    assert format_result_for_llm(payload) == str(payload)


def test_the_keys_dropped_by_the_incident_are_covered():
    for key in ("partial_data", "reason", "return_code", "working_directory", "hint", "session_id"):
        assert key in _RESULT_DETAIL_KEYS, key


# ── failure_key ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("chk5", "chk24"),
        # The letters matter: digit masking would accidentally equalise the pair
        # above, so an id scheme without digits is the case that actually proves
        # the key ignores the attempt rather than merely scrubbing its numbers.
        ("sess-alpha", "sess-omega"),
    ],
)
def test_the_failure_key_ignores_which_shell_produced_it(first, second):
    """The retry mints a fresh session_id; that must not read as a new problem."""
    a = failure_key({**ABORTED_SHELL_RESULT, "session_id": first})
    b = failure_key({**ABORTED_SHELL_RESULT, "session_id": second})
    assert a == b


def test_the_failure_key_survives_a_changed_exit_code():
    """Same crash, different exit code — one complaint, one streak."""
    crash_1 = {**ABORTED_SHELL_RESULT, "return_code": 1}
    crash_9009 = {
        **ABORTED_SHELL_RESULT,
        "return_code": 9009,
        "message": (
            "Command aborted: the shell process exited on its own (exit code 9009) before the command finished."
        ),
    }
    assert failure_key(crash_1) == failure_key(crash_9009)


def test_the_failure_key_separates_different_reasons():
    """A deliberate stop must never be counted as "the shell died again"."""
    stopped = {**ABORTED_SHELL_RESULT, "reason": "session_stopped"}
    assert failure_key(ABORTED_SHELL_RESULT) != failure_key(stopped)


def test_the_failure_key_separates_different_statuses():
    assert failure_key({"status": "error", "message": "boom"}) != failure_key({"status": "timeout", "message": "boom"})


def test_the_failure_key_separates_different_messages():
    assert failure_key({"status": "error", "message": "disk full"}) != failure_key(
        {"status": "error", "message": "permission denied"}
    )


def test_the_failure_key_still_masks_incidental_numbers():
    assert failure_key({"status": "error", "message": "boom at line 41"}) == failure_key(
        {"status": "error", "message": "boom at line 99"}
    )


def test_the_failure_key_falls_back_for_text_and_bare_results():
    assert failure_key("Error: nope") == failure_key("Error: nope")
    assert failure_key("Error: nope") != failure_key("Error: other")
    assert failure_key(42) == failure_key(42)


def test_the_failure_key_prefers_the_taxonomy_over_the_prose():
    """When `reason` is present it is the identity — the prose may be reworded."""
    a = {"status": "error", "reason": "shell_exited", "message": "the shell died"}
    b = {"status": "error", "reason": "shell_exited", "message": "the shell exited unexpectedly"}
    assert failure_key(a) == failure_key(b)


# ── is_failure_result ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"status": "error", "message": "x"}, True),
        ({"status": "failed"}, True),
        ({"status": "FAILURE"}, True),
        ({"aborted": True}, True),
        ({"timed_out": True}, True),
        ("Error: nope", True),
        ({"status": "success"}, False),
        # "still running" is a legitimate poll result, not a failure.
        ({"status": "success", "completed": False, "message": "still running"}, False),
        ({"status": "success", "completed": True}, False),
        ("plain text", False),
        (None, False),
        (42, False),
        (["a"], False),
    ],
)
def test_is_failure_result(result, expected):
    assert is_failure_result(result) is expected


# ── structural fences — the fixes must not be quietly reverted ─────────────


def test_turn_loop_never_collapses_a_result_to_message_alone():
    assert "format_result_for_llm(result)" in TURN_LOOP_SRC, "the collapse helper is no longer called"
    assert not re.search(r"_display\s*=\s*result\.get\(\"message\"\)", TURN_LOOP_SRC), (
        "the message-only collapse is back: partial_data / reason / return_code get dropped again"
    )


def test_every_collected_tool_result_carries_its_failure_flag():
    """The FAILURE guard signal reads `failed`; dropping it disables the signal."""
    assert '"failed": is_failure_result(result),' in TURN_LOOP_SRC


def test_the_guard_is_handed_the_stable_failure_key():
    """Digesting the rendered text instead is what left the guard dead for 40 rounds."""
    assert "from opensquad._runner._result_formatter import failure_key" in TURN_LOOP_SRC
    assert '"failure_key": failure_key(result),' in TURN_LOOP_SRC


def test_the_repeat_guard_reads_the_pure_core():
    assert "_repeat_guard.evaluate(" in TURN_LOOP_SRC
    assert "_repeat_guard.abort_message(" in TURN_LOOP_SRC


def test_shell_aborts_no_longer_share_one_opaque_message():
    assert "Command aborted (shell closed or process exited)" not in SYSTEM_SRC


@pytest.mark.parametrize(
    "reason",
    ["shell_not_running", "session_stopped", "shell_exited", "user_stop", "timeout"],
)
def test_every_abort_path_reports_a_distinct_reason(reason):
    assert f'"reason": "{reason}"' in SYSTEM_SRC


def _slice(src: str, start_marker: str, end_marker: str) -> str:
    """Source between two markers; '' when either is missing (so the lock fails loud).

    Markers must be unique — an ambiguous anchor (e.g. grepping globally for a
    reason string) is exactly how a fence turns decorative.
    """
    try:
        start = src.index(start_marker)
        end = src.index(end_marker, start)
    except ValueError:
        return ""
    return src[start:end]


def _stop_branch() -> str:
    return _slice(SYSTEM_SRC, "if self._stop_event.is_set():", "_exit_code = self.process.returncode")


def _crash_branch() -> str:
    return _slice(SYSTEM_SRC, "_exit_code = self.process.returncode", "if _user_stop_requested():")


def test_a_deliberate_stop_reports_its_own_reason():
    """`close()` also kills the process — only the reason keeps the two apart."""
    body = _stop_branch()
    assert '"reason": "session_stopped"' in body
    assert "shell_exited" not in body
    assert "session was stopped" in body


def test_a_dead_shell_reports_the_crash_reason():
    body = _crash_branch()
    assert '"reason": "shell_exited"' in body
    assert "session_stopped" not in body
    assert '"return_code": _exit_code' in body


def test_the_stop_branch_is_checked_before_the_crash_branch():
    assert "if self._stop_event.is_set():" in SYSTEM_SRC, "the deliberate-stop branch is gone"
    assert SYSTEM_SRC.index("if self._stop_event.is_set():") < SYSTEM_SRC.index(
        "_exit_code = self.process.returncode"
    ), "a user stop would be misreported as 'the shell died on its own'"


def test_a_deliberate_stop_does_not_resurrect_the_shell():
    """Recycling there would silently undo the user's stop."""
    body = _stop_branch()
    assert body, "the deliberate-stop branch is gone"
    assert "_recycle(" not in body


def test_every_abort_path_returns_the_captured_output():
    assert SYSTEM_SRC.count('"partial_data": partial') >= 4
