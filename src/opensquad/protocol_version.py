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
