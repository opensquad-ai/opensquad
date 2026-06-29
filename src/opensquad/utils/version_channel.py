"""Release-channel detection for OpenSquad version strings.

Used by the gateway's ``/version`` endpoint to decide whether to query
GitHub for an update notification. Update checks are only meaningful
for ``stable`` builds; on ``dev`` / ``pre-release`` / ``local`` /
``unknown`` channels the user already knows they are on a non-final
build and would receive misleading "new version" hints otherwise.

The channel is derived from the PEP 440 version string reported by
``opensquad.__version__`` — the same value the install uses for its
own version comparison.

Channels:
  - ``stable``       final or post-release (e.g. ``0.1.0``,
                     ``0.1.0.post0``)
  - ``dev``          dev release (e.g. ``0.2.0.dev0``) — used on
                     dev / hotfix branches
  - ``pre-release``  alpha / beta / rc (e.g. ``0.1.0b1``) — opt-in
                     previews
  - ``local``        has a PEP 440 local segment
                     (e.g. ``0.1.0+local.abc``) — local builds
  - ``unknown``      unparseable / missing
"""
from __future__ import annotations

from typing import Literal

Channel = Literal["stable", "dev", "pre-release", "local", "unknown"]

# Channels for which the update-check GitHub lookup is meaningful.
CHECKABLE_CHANNELS: frozenset[str] = frozenset({"stable"})


def detect_channel(version: str) -> Channel:
    """Return the release channel implied by a PEP 440 version string.

    Falls back to ``"unknown"`` for empty / unparseable input so the
    caller can treat it the same as dev: don't bother the user with
    an update prompt.
    """
    if not version or version == "unknown":
        return "unknown"
    try:
        from packaging.version import parse  # local import: lightweight

        v = parse(version)
    except Exception:
        return "unknown"

    if v.is_devrelease:           # X.Y.Z.devN  -> dev branch
        return "dev"
    if v.is_prerelease:           # a / b / rc
        return "pre-release"
    if v.local is not None:       # +local.foo
        return "local"
    return "stable"


def should_check_for_updates(version: str) -> tuple[bool, Channel, str | None]:
    """Decide whether ``/version`` should query GitHub.

    Returns ``(check, channel, skip_reason)``. When ``check`` is False,
    ``skip_reason`` is a human-readable explanation suitable for the
    UI; when ``check`` is True, ``skip_reason`` is None.
    """
    channel = detect_channel(version)
    if channel in CHECKABLE_CHANNELS:
        return True, channel, None
    return (
        False,
        channel,
        (
            f"Update check is disabled for {channel} builds "
            f"({version}). Switch to a stable release to receive update "
            f"notifications."
        ),
    )
