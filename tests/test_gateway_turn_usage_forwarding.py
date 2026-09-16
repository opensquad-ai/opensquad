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

There are TWO allow-lists in the gateway's dispatcher and a type must be in both:

1. ``_AGENT_OUTPUT_BROADCAST_TYPES`` — decides broadcast-to-every-pane vs
   forward-to-one-user.
2. the dispatch gate in ``_agent_message_loop`` — decides whether the frame is
   handled at all (else it hits the ``Unknown message`` warning and is dropped).

Both are now *derived* from ``opensquad.protocol_version`` (see
``tests/test_ws_event_contract.py`` for the single-source lock). This file keeps
the original, narrower concern: the turn-lifecycle core specifically must be in
both, and the gateway must still bind the constants rather than growing its own
divergent copy.
"""

from __future__ import annotations

import ast
from pathlib import Path

from opensquad import protocol_version as protocol

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


def _gateway_protocol_imports() -> set[str]:
    """Names websocket.py binds from opensquad.protocol_version."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(_source())):
        if isinstance(node, ast.ImportFrom) and node.module == "opensquad.protocol_version":
            names |= {a.name for a in node.names}
    return names


def _broadcast_set() -> set[str]:
    """The gateway's broadcast allow-list.

    The value comes from the single source; the gateway's own inline copy is
    gone, so we also assert it still *binds* the constant. Without that import
    check this test would happily pass while the dispatcher used a stale,
    hand-written frozenset behind it.
    """
    assert "AGENT_OUTPUT_BROADCAST_TYPES" in _gateway_protocol_imports(), (
        "websocket.py no longer binds AGENT_OUTPUT_BROADCAST_TYPES from "
        "opensquad.protocol_version — it must not carry its own copy"
    )
    return set(protocol.AGENT_OUTPUT_BROADCAST_TYPES)


def _dispatch_list() -> set[str]:
    """The dispatch gate's allow-list (resolved from the same single source)."""
    assert "AGENT_OUTPUT_DISPATCH_TYPES" in _gateway_protocol_imports(), (
        "websocket.py no longer binds AGENT_OUTPUT_DISPATCH_TYPES from "
        "opensquad.protocol_version — the dispatch gate must compare against it"
    )
    return set(protocol.AGENT_OUTPUT_DISPATCH_TYPES)


def test_source_file_exists() -> None:
    assert _WEBSOCKET_PY.is_file(), f"missing {_WEBSOCKET_PY}"


def test_the_two_decisions_use_their_own_constant() -> None:
    """Guards the test itself: the two allow-lists must stay separately sourced.

    Both lists now resolve to the same *value* (every broadcast type is also
    dispatched — see the ``BROADCAST <= DISPATCH`` invariant), so "the two sets
    differ" is no longer a usable anti-vacuity check. The source-level check
    below replaces it: the dispatcher must still make its two decisions against
    two named constants, so this file cannot pass by inspecting one list twice.
    """
    src = _source()
    assert "msg_type in _AGENT_OUTPUT_BROADCAST_TYPES" in src, (
        "the broadcast decision no longer references _AGENT_OUTPUT_BROADCAST_TYPES"
    )
    assert "msg_type in _AGENT_OUTPUT_DISPATCH_TYPES" in src, (
        "the dispatch gate no longer references _AGENT_OUTPUT_DISPATCH_TYPES"
    )
    # ...and a broadcast type the gate would drop is impossible by construction.
    assert _broadcast_set() <= _dispatch_list(), (
        "a broadcast-eligible type is missing from the dispatch gate — the frame "
        "is dropped before the broadcast decision is even reached"
    )


def test_turn_usage_reaches_the_browser() -> None:
    """The regression: `turn_usage` must survive BOTH gateway allow-lists."""
    broadcast = _broadcast_set()
    dispatch = _dispatch_list()
    assert "turn_usage" in broadcast, (
        "turn_usage missing from AGENT_OUTPUT_BROADCAST_TYPES — other panes would "
        "never see the round's token usage live"
    )
    assert "turn_usage" in dispatch, (
        "turn_usage missing from the dispatch gate — the frame is logged as "
        "'Unknown message from agent' and dropped (消耗 badge never appears)"
    )


def test_turn_cancelled_reaches_the_browser() -> None:
    """The other half of the same bug class.

    ``turn_cancelled`` was broadcast-only, so the frame was dropped at the gate
    even though ``useAgentWebSocket`` has an ``onWs('turn_cancelled')`` handler.
    """
    assert "turn_cancelled" in _broadcast_set()
    assert "turn_cancelled" in _dispatch_list(), (
        "turn_cancelled missing from the dispatch gate — the frontend handler can "
        "never fire because the frame is dropped at the gateway"
    )


def test_turn_lifecycle_types_agree_across_both_allow_lists() -> None:
    """Keep the two lists in sync for the turn-lifecycle core.

    They live in different places in the file and drifted apart once already
    (`turn_usage` was in neither), so pin the shared core explicitly.
    """
    broadcast = _broadcast_set()
    dispatch = _dispatch_list()
    for msg_type in _REQUIRED_IN_BOTH:
        assert msg_type in broadcast, f"{msg_type} missing from AGENT_OUTPUT_BROADCAST_TYPES"
        assert msg_type in dispatch, f"{msg_type} missing from the dispatch allow-list"
