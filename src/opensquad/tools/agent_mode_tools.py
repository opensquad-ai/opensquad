"""Agent mode tools — request Plan/Build switches (user must approve in UI)."""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


async def request_switch(target_mode: str, reason: str = "") -> str:
    """Request switching between Plan and Build modes.

    Emits an approval card to the chat UI. Does NOT change mode until the user
    clicks Approve. Use this when you need the other mode's capabilities.

    Args:
        target_mode: ``plan`` (read-only explore/plan) or ``build`` (edit/run tools).
        reason: Short explanation shown to the user on the approval card.
    """
    from opensquad.agent_mode import (
        MODE_BUILD,
        MODE_PLAN,
        get_current_mode,
        normalize_mode,
    )
    from opensquad.events import bus

    target = normalize_mode(target_mode)
    current = get_current_mode()
    if target == current:
        return f"Already in {current} mode. No switch needed."

    if target not in (MODE_PLAN, MODE_BUILD):
        return 'Invalid target_mode. Use "plan" or "build".'

    req_id = str(uuid.uuid4())
    reason_text = (reason or "").strip() or (
        "Switch to Build to edit files / run commands"
        if target == MODE_BUILD
        else "Switch to Plan for read-only exploration"
    )

    try:
        await bus.emit_async(
            "info",
            {
                "event": "mode_switch_approval",
                "id": req_id,
                "from_mode": current,
                "to_mode": target,
                "reason": reason_text,
                "status": "pending",
                "text": f"Request to switch {current} → {target}: {reason_text}",
            },
        )
    except Exception as e:
        logger.warning("[agent_mode] Failed to emit approval request: %s", e)
        return f"Failed to request mode switch: {e}"

    return (
        f"Mode switch requested: {current} → {target}. "
        "Waiting for the user to Approve or Deny in the chat UI. "
        "Do not assume the new mode is active yet. "
        "Stop this turn after telling the user to Approve/Deny — "
        "when they decide, you will automatically receive a system message to continue."
    )
