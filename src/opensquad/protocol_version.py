"""
API / Protocol version control for OpenSquad WebSocket messages.

Problem:
  - Gateway and Agent evolve independently
  - Adding new fields breaks old peers that strict-validate JSON
  - Removing fields breaks old peers that depend on them

Solution:
  - Every message carries a protocol version (e.g. "v=1")
  - Peers negotiate the highest mutually-supported version at handshake
  - Unknown fields are ignored (forward compatibility)
  - Missing fields fall back to defaults (backward compatibility)

Version history:
  v1 (legacy): basic chat / command / heartbeat, no seq, no version field
  v2 (current): adds seq (dedup), timestamp as ISO-8601, version field
  v3 (future):  adds batching, compression, binary frames
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Version constants
# ---------------------------------------------------------------------------

CURRENT_VERSION: int = 2
MIN_SUPPORTED_VERSION: int = 1


def version_string(v: int) -> str:
    return f"v{v}"


# ===========================================================================
# WebSocket event-type contract
# ===========================================================================
#
# ONE enumeration that every registration point derives from.  There are three
# of them, in two different processes:
#
#   1. launcher  — ``GatewayAdapter._relay_subscriptions()`` bus topics
#                  (which agent events get relayed to the gateway at all)
#   2. gateway   — ``_AGENT_OUTPUT_BROADCAST_TYPES`` (broadcast to every pane vs
#                  a directed push to one user)
#   3. gateway   — the ``elif msg_type in ...`` dispatch gate inside
#                  ``_agent_message_loop`` (handle the frame vs drop it)
#
# Bug class this removes: registering a type at one point and forgetting
# another.  The frame then falls through to
# ``logger.warning("Unknown message from agent ...")`` and vanishes — the event
# is on disk, the wire looks healthy, and only the UI is silently empty:
#
#   * ``turn_usage``      — was missing from BOTH gateway lists (消耗 badge dead)
#   * ``turn_cancelled``  — was missing from the dispatch gate only (the
#                           frontend has a handler for it; the frame was dropped
#                           before ever reaching the browser)
#   * ``to_user_end_task``— same shape as ``turn_cancelled``, but worse: a turn
#                           ending on the end-task tag emits it as its ONLY
#                           final frame, so the Web UI streamed the answer and
#                           then never cleared the streaming cursor.  Users had
#                           to press Stop to unstick the pane.
#
# Derived-only rule: never hand-write a type list at a registration point.
# ``tests/test_ws_event_contract.py`` fails if a literal reappears.

# Every agent-origin WS message type — what an agent can send up to the gateway
# (directly, or relayed by the launcher-side adapter).  Browser->gateway and
# gateway->agent frames are a different namespace and deliberately absent.
EVENT_TYPES: frozenset[str] = frozenset(
    {
        # ── chat / stream output ──────────────────────────────────────────
        "message",
        "response",
        "to_user_end_task",
        "thought",
        "stream",
        "steer_consumed",
        "tool_call",
        "tool_call_delta",
        "tool_result",
        # ── agent / session lifecycle ─────────────────────────────────────
        "state",
        "wake",
        "sleep",
        "info",
        "status",
        "turn_start",
        "turn_elapsed",
        "turn_usage",
        "turn_cancelled",
        "token_stats",
        "current_session",
        "history_sync",
        "session_list",
        "busy_sessions",
        "primary_session",
        # ── workflow / artifacts ──────────────────────────────────────────
        "file_push",
        "plan",
        "prompt_update",
        "output_media",
        "summary_stream",
        "compression_progress",
        # ── background jobs ──────────────────────────────────────────────
        "job_stdout",
        "job_status",
        # ── realtime voice ───────────────────────────────────────────────
        "voice_realtime_status",
        "voice_audio_out",
        "voice_transcript",
        # ── scheduled tasks ──────────────────────────────────────────────
        "scheduled_task_turn_done",
        # ── parallel task panel (M2) ─────────────────────────────────────
        "task_update",
        "task_removed",
        "task_command_result",
        # ── launcher relayed, not part of the agent-output dispatch gate ──
        "agent_ready_stage",
        "group_member_update",
        "user_status_update",
        # ── control ──────────────────────────────────────────────────────
        "pong",
    }
)

# Gateway policy — which of the above must reach EVERY connected pane instead of
# being pushed to the one user that owns the turn.  An agent's disk session is a
# shared workspace: a turn started from the TUI has to stream live into the Web
# UI of the same session and vice versa, so a user-directed push would hide the
# whole run from every other client (cross-account desync).
AGENT_OUTPUT_BROADCAST_TYPES: frozenset[str] = frozenset(
    {
        "message",
        "response",
        # A turn that ends on the end-task tag emits this INSTEAD of
        # ``to_user_final`` — it is the terminal frame of that turn, and the
        # frontend folds the task process on it.  Not broadcast-eligible meant
        # the frame was dropped at the gate and the pane stayed "streaming"
        # forever.
        "to_user_end_task",
        "thought",
        "stream",
        "steer_consumed",
        "tool_call",
        "tool_call_delta",
        "tool_result",
        "state",
        "wake",
        "sleep",
        "info",
        "status",
        "turn_start",
        "turn_elapsed",
        "turn_usage",
        "turn_cancelled",
        "token_stats",
        "current_session",
        "history_sync",
        "session_list",
        "busy_sessions",
        "primary_session",
        "file_push",
        "plan",
        "prompt_update",
        "output_media",
        "summary_stream",
        "compression_progress",
        "job_stdout",
        "job_status",
        "voice_realtime_status",
        "voice_audio_out",
        "voice_transcript",
        "scheduled_task_turn_done",
        # M2 parallel task lifecycle — the task panel is a per-agent shared view.
        "task_update",
        "task_removed",
    }
)

# Gateway policy — which of the above the ``_agent_message_loop`` dispatch gate
# actually handles.  A type absent from this set is logged as
# "Unknown message from agent" and thrown away.
#
# Invariant: ``AGENT_OUTPUT_BROADCAST_TYPES <= AGENT_OUTPUT_DISPATCH_TYPES``.  A
# broadcast type missing from the gate could never broadcast at all — the frame
# is dropped one branch earlier, which is exactly what used to happen to
# ``turn_cancelled`` (broadcast-eligible, therefore listed here, but absent from
# the gate so its frontend handler could never fire).
#
# The two sets are kept as separate names because they answer different
# questions (reaches every pane? vs handled at all?), and the gate may legitimately
# diverge in either direction later.  Today they are equal.
AGENT_OUTPUT_DISPATCH_TYPES: frozenset[str] = frozenset(set(AGENT_OUTPUT_BROADCAST_TYPES) | {"turn_cancelled"})

# Relayed by the launcher adapter but intentionally NOT handled by the gateway's
# agent-output gate: these are relay diagnostics, not agent output.  Listed
# explicitly so that ``relayed ⊆ dispatch | exempt`` can be asserted — a newly
# relayed type that the gateway would silently drop therefore fails the guard
# instead of disappearing.
#
# NOTE (resolved 2026-09-21): ``to_user_end_task`` used to be listed here.  It is
# agent output — the terminal frame of an end-task turn — not a relay
# diagnostic, and the dispatch body already had a branch for it (the
# history-cache write), which was therefore unreachable.  It now lives in
# ``AGENT_OUTPUT_BROADCAST_TYPES`` above, so that branch runs again.
RELAYED_WITHOUT_DISPATCH: frozenset[str] = frozenset(
    {
        "agent_ready_stage",
        "group_member_update",
        "user_status_update",
    }
)

# ---------------------------------------------------------------------------
# Launcher relay contract
# ---------------------------------------------------------------------------

# Agent event-bus topics that are renamed on the way out.  The launcher adapter
# subscribes to the topic on the left and emits a WS frame whose ``type`` is the
# value on the right.
BUS_TOPIC_TO_WS_TYPE: dict[str, str] = {
    "to_user_final": "message",
    "to_user_reply": "message",
    "to_user_end_task": "to_user_end_task",
    "thought": "thought",
    "to_user_stream": "stream",
    "tool_call": "tool_call",
    "tool_call_delta": "tool_call_delta",
    "tool_result": "tool_result",
    "state": "state",
}

# Agent event-bus topics forwarded verbatim — the WS ``type`` is the topic name.
GENERIC_RELAY_TOPICS: tuple[str, ...] = (
    "wake",
    "sleep",
    "info",
    "status",
    "steer_consumed",
    "turn_start",
    "token_stats",
    "current_session",
    "history_sync",
    "agent_ready_stage",
    "session_list",
    "plan",
    "turn_elapsed",
    "turn_usage",
    "turn_cancelled",
    "prompt_update",
    "output_media",
    "voice_audio_out",
    "voice_transcript",
    "voice_realtime_status",
    "summary_stream",
    "group_member_update",
    "user_status_update",
    "job_stdout",
    "job_status",
    "busy_sessions",
    "scheduled_task_turn_done",
    "compression_progress",
)

# The relay topic list in subscription order.  The launcher iterates this and
# binds a handler per topic, so it can never subscribe to something the gateway
# does not recognise without tripping the guard.
LAUNCHER_RELAY_TOPICS: tuple[str, ...] = tuple(BUS_TOPIC_TO_WS_TYPE) + GENERIC_RELAY_TOPICS


def relayed_event_types() -> frozenset[str]:
    """The WS types the launcher relay can produce."""
    return frozenset(BUS_TOPIC_TO_WS_TYPE.values()) | frozenset(GENERIC_RELAY_TOPICS)


# ---------------------------------------------------------------------------
# WS frame field names
# ---------------------------------------------------------------------------
#
# The wire format is shared with TypeScript and written by hand in several
# places, so a renamed key silently breaks one side only.  Real incident:
# ``compression_progress`` was produced with ``isFinal`` (camelCase) while every
# consumer read ``data.is_final`` (snake_case), so ``isWorkflowSettled`` never
# settled a compression block.  Producers must use these constants; the
# TypeScript mirror is ``nexuschat-pro/utils/wsFieldNames.ts`` and
# ``tests/test_ws_event_contract.py`` asserts the two stay byte-identical.

FIELD_VERSION = "v"
FIELD_SEQ = "seq"
FIELD_TIMESTAMP = "timestamp"
FIELD_TYPE = "type"
FIELD_ACTION = "action"
FIELD_SID = "sid"
FIELD_SESSION_ID = "session_id"
FIELD_AGENT_ID = "agent_id"
FIELD_USER_ID = "user_id"
FIELD_CONTENT = "content"
FIELD_DATA = "data"
FIELD_TURN_ID = "turn_id"
FIELD_ROUND_ID = "round_id"
FIELD_TRACE_ID = "trace_id"
FIELD_TEXT = "text"
FIELD_IS_FINAL = "is_final"

# Every field name above, for the cross-language spelling check.
WS_FIELD_NAMES: frozenset[str] = frozenset(
    {
        FIELD_VERSION,
        FIELD_SEQ,
        FIELD_TIMESTAMP,
        FIELD_TYPE,
        FIELD_ACTION,
        FIELD_SID,
        FIELD_SESSION_ID,
        FIELD_AGENT_ID,
        FIELD_USER_ID,
        FIELD_CONTENT,
        FIELD_DATA,
        FIELD_TURN_ID,
        FIELD_ROUND_ID,
        FIELD_TRACE_ID,
        FIELD_TEXT,
        FIELD_IS_FINAL,
    }
)


# ---------------------------------------------------------------------------
# Negotiation
# ---------------------------------------------------------------------------


def negotiate_version(peer_versions: list[int]) -> int:
    """Pick the highest version supported by both sides.

    Args:
        peer_versions: List of versions the remote peer supports.

    Returns:
        The negotiated version (>= MIN_SUPPORTED_VERSION).
        Falls back to MIN_SUPPORTED_VERSION if no overlap.
    """
    supported = set(range(MIN_SUPPORTED_VERSION, CURRENT_VERSION + 1))
    common = supported.intersection(peer_versions)
    if common:
        return max(common)
    return MIN_SUPPORTED_VERSION


# ---------------------------------------------------------------------------
# Message wrapping / unwrapping
# ---------------------------------------------------------------------------


def wrap_message(payload: dict[str, Any], version: int | None = None) -> dict[str, Any]:
    """Wrap a payload with protocol metadata.

    Args:
        payload: The original message dict.
        version: Explicit version to use (defaults to CURRENT_VERSION).

    Returns:
        A new dict with the payload merged and a 'v' field added.
        If payload already contains 'v', it is respected.
    """
    v = version or payload.get("v") or CURRENT_VERSION
    # Only add version field if not already present (allows override)
    if "v" not in payload:
        payload = {**payload, "v": v}
    return payload


def unwrap_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Extract payload from a versioned message.

    Returns the message with version metadata normalized.
    If no 'v' field is present, assumes v1 (legacy).
    """
    if "v" not in msg:
        msg = {**msg, "v": 1}
    return msg


def get_message_version(msg: dict[str, Any]) -> int:
    """Return the protocol version of a message (defaults to 1)."""
    v = msg.get("v")
    if isinstance(v, int) and v >= 1:
        return v
    # Legacy string versions (e.g. "v2") — parse numeric part
    if isinstance(v, str) and v.startswith("v"):
        try:
            return int(v[1:])
        except ValueError:
            pass
    return 1


# ---------------------------------------------------------------------------
# Compatibility helpers
# ---------------------------------------------------------------------------


def normalize_v1_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Convert a v1 (legacy) message to v2-compatible shape.

    - Adds seq=0 if missing (v1 had no dedup)
    - Ensures timestamp is present
    """
    if "seq" not in msg:
        msg["seq"] = 0
    if "timestamp" not in msg:
        from opensquad.time_utils import utc_now_iso

        msg["timestamp"] = utc_now_iso()
    return msg


def downgrade_message(msg: dict[str, Any], target_version: int) -> dict[str, Any]:
    """Strip fields that are unknown to an older peer.

    Args:
        msg: The outgoing message (assumed current version).
        target_version: The version the remote peer understands.

    Returns:
        A copy of the message with fields removed that the target
        version does not support.
    """
    if target_version >= CURRENT_VERSION:
        return dict(msg)

    result = dict(msg)
    # v1 does not understand 'seq' or 'v'
    if target_version < 2:
        result.pop("seq", None)
        result.pop("v", None)
    return result
