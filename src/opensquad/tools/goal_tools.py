"""Goal tools — progress / achieve / blocked signals for /goal mode."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def update_progress(note: str) -> str:
    """Record progress toward the active /goal (shown in GOAL_STATE).

    Args:
        note: Short description of what was just accomplished or verified.
    """
    from opensquad.goal_mode import notify_goal_changed
    from opensquad.goal_mode import update_progress as _upd

    result = _upd(note)
    if not result.get("ok"):
        return f"Failed: {result.get('error', 'unknown')}"
    await notify_goal_changed(result.get("goal"), text="Goal progress updated")
    return f"Progress recorded: {(note or '').strip()[:500]}"


async def mark_achieved(evidence: str) -> str:
    """Declare the active /goal complete with verification evidence.

    Only valid while status is pursuing. Stops automatic goal continuation.

    Args:
        evidence: Concrete proof the objective is met (test output, metrics, checklist).
    """
    from opensquad.goal_mode import mark_achieved as _mark
    from opensquad.goal_mode import notify_goal_changed

    result = _mark(evidence)
    if not result.get("ok"):
        return f"Failed: {result.get('error', 'unknown')}"
    await notify_goal_changed(result.get("goal"), text="Goal achieved")
    return f"Goal marked achieved. Automatic continuation stopped. Evidence: {(evidence or '').strip()[:800]}"


async def report_blocked(reason: str) -> str:
    """Report a blocker on the active /goal without clearing it.

    Args:
        reason: What is blocking progress and what you need from the user (if anything).
    """
    from opensquad.goal_mode import notify_goal_changed
    from opensquad.goal_mode import report_blocked as _block

    result = _block(reason)
    if not result.get("ok"):
        return f"Failed: {result.get('error', 'unknown')}"
    await notify_goal_changed(result.get("goal"), text="Goal blocked")
    return f"Blocker recorded: {(reason or '').strip()[:800]}"
