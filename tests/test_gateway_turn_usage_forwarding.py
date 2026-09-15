"""Regression lock: the gateway must FORWARD ``turn_usage`` to browser clients.

Root cause this guards
----------------------
The agent emits a ``turn_usage`` event (per-round billed tokens, consumed by the
frontend's 消耗 badge). The launcher-side adapter subscribed and forwarded it,
but the gateway's agent-message dispatcher has its own allow-lists and
``turn_usage`` was missing from both, so every frame was swallowed by the
``Unknown message from agent`` branch::

    2026-09-14 14:24:03 - app.ai_web.websocket - WARNING -
    Unknown message from agent agent305-001: {'type': 'turn_usage', ...}

The badge therefore never appeared live, even though the event was on disk.

There are TWO allow-lists in ``websocket.py`` and a type must be in both:

1. ``_AGENT_OUTPUT_BROADCAST_TYPES`` — decides broadcast-to-every-pane vs
   forward-to-one-user.
2. the ``elif msg_type in [...]`` gate in ``_agent_message_loop`` — decides
   whether the frame is handled at all (else it hits the ``Unknown message``
   warning and is dropped).

Locking this at the source level (rather than importing the FastAPI module) keeps
the test dependency-free while still failing if either list loses the entry.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_WEBSOCKET_PY = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "opensquad"
    / "gateway"
    / "backend"
    / "app"
    / "ai_web"
    / "websocket.py"
)

# Types that MUST both be broadcast to every pane and pass the dispatch gate:
# they carry turn-lifecycle data that every open chat pane needs live.
_REQUIRED_IN_BOTH = ("turn_start", "turn_elapsed", "turn_usage", "token_stats")


def _source() -> str:
    return _WEBSOCKET_PY.read_text(encoding="utf-8")


def _string_literals(node: ast.AST) -> set[str]:
    """The plain string constants of a set/list/tuple literal."""
    assert isinstance(node, (ast.Set, ast.List, ast.Tuple)), node
    return {elt.value for elt in node.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)}


def _broadcast_set() -> set[str]:
    """The literal passed to ``frozenset(...)`` for the broadcast allow-list."""
    tree = ast.parse(_source())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "frozenset"
            and node.value.args
            and isinstance(node.value.args[0], ast.Set)
        ):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if "_AGENT_OUTPUT_BROADCAST_TYPES" in targets:
                return _string_literals(node.value.args[0])
    pytest.fail("_AGENT_OUTPUT_BROADCAST_TYPES frozenset(...) literal not found in websocket.py")


def _dispatch_list() -> set[str]:
    """The literal of the ``elif msg_type in [...]`` dispatch gate.

    Must be located structurally (the list is the right-hand comparator of an
    ``in`` test on ``msg_type``). A plain "first literal containing turn_elapsed"
    search silently matches the *frozenset* literal, which sits earlier in the
    file — the gate would then never be inspected and removing `turn_usage` from
    it would pass unnoticed.
    """
    tree = ast.parse(_source())
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "msg_type"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.In)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], (ast.List, ast.Set, ast.Tuple))
        ):
            values = _string_literals(test.comparators[0])
            if "turn_elapsed" in values:
                return values
    pytest.fail("the `elif msg_type in [...]` dispatch allow-list was not found in websocket.py")


def test_source_file_exists() -> None:
    assert _WEBSOCKET_PY.is_file(), f"missing {_WEBSOCKET_PY}"


def test_the_two_allow_lists_are_distinct_literals() -> None:
    """Sanity: the id-identity of the analysed literal must not overlap.

    Guards the test itself: if both helpers returned the same literal (the
    frozenset), the "agree across both lists" check below would be vacuous.
    """
    broadcast = _broadcast_set()
    dispatch = _dispatch_list()
    # The dispatch gate is a superset of the broadcast core in the current code;
    # what matters is that we resolved it from the `elif msg_type in` test, so
    # the two helpers cannot be reading the same node. Verified structurally by
    # asserting the dispatch gate also carries gate-only types.
    assert "compression_progress" in dispatch, "dispatch gate resolved from the wrong literal"
    assert broadcast != dispatch or "compression_progress" not in broadcast, (
        "the dispatch gate and the broadcast frozenset resolved to the same literal — the elif-list check is vacuous"
    )


def test_turn_usage_reaches_the_browser() -> None:
    """The regression: `turn_usage` must survive BOTH gateway allow-lists."""
    broadcast = _broadcast_set()
    dispatch = _dispatch_list()
    assert "turn_usage" in broadcast, (
        "turn_usage missing from _AGENT_OUTPUT_BROADCAST_TYPES — other panes would "
        "never see the round's token usage live"
    )
    assert "turn_usage" in dispatch, (
        "turn_usage missing from the `elif msg_type in [...]` dispatch gate — the frame "
        "is logged as 'Unknown message from agent' and dropped (消耗 badge never appears)"
    )


def test_turn_lifecycle_types_agree_across_both_allow_lists() -> None:
    """Keep the two lists in sync for the turn-lifecycle core.

    They live in different places in the file and drifted apart once already
    (`turn_usage` was in neither), so pin the shared core explicitly.
    """
    broadcast = _broadcast_set()
    dispatch = _dispatch_list()
    for msg_type in _REQUIRED_IN_BOTH:
        assert msg_type in broadcast, f"{msg_type} missing from _AGENT_OUTPUT_BROADCAST_TYPES"
        assert msg_type in dispatch, f"{msg_type} missing from the dispatch allow-list"
