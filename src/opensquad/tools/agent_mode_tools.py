"""Agent mode tools — request Plan/Build switches (user must approve in UI / group)."""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


async def request_switch(target_mode: str, reason: str = "", group_id: str = "") -> str:
    """Request switching between Plan and Build modes.

    Emits an approval card. Does NOT change mode until the user clicks Approve.

    - In **private AI chat**: card appears above the composer (web UI).
    - In **group chat** (or when ``group_id`` is set): also posts a 确定/拒绝 card
      in the group via ``im.request_approval``. Prefer the group card when the
      conversation is happening in a group.

    Args:
        target_mode: ``plan`` (read-only explore/plan) or ``build`` (edit/run tools).
        reason: Short explanation shown on the approval card.
        group_id: Optional group id/name. If empty and the current turn is from a
            group, the active group is used automatically.
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

    # Always emit bus event so private AI-chat UI can show a card too
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

    # Prefer group card when talking in a group
    group_posted = False
    group_note = ""
    try:
        from opensquad.collab_approval import resolve_current_group_id
        from opensquad.tools.im import request_approval

        gid = resolve_current_group_id(group_id)
        if gid:
            result = request_approval(
                title=f"切换模式：{current} → {target}",
                summary=reason_text,
                kind="mode_switch",
                group_id=gid,
                to_mode=target,
                from_mode=current,
                approval_id=req_id,
            )
            if isinstance(result, dict) and result.get("status") == "pending":
                group_posted = True
                # Prefer the group approval id so resolve applies set_agent_mode correctly
                group_note = (
                    f" A 确定/拒绝 card was also posted to group {result.get('group_id') or gid} "
                    f"(approval_id={result.get('approval_id')}). Prefer approving there when chatting in the group."
                )
            elif isinstance(result, dict) and result.get("status") == "error":
                logger.warning("[agent_mode] group approval card failed: %s", result.get("message"))
    except Exception as e:
        logger.warning("[agent_mode] group approval path failed: %s", e)

    wait_where = (
        "Waiting for the user to Approve/Deny in the **group chat card** (or the AI chat UI)."
        if group_posted
        else "Waiting for the user to Approve or Deny in the chat UI."
    )
    return (
        f"Mode switch requested: {current} → {target}. "
        f"{wait_where} "
        "Do not assume the new mode is active yet. "
        "Stop this turn after telling the user to Approve/Deny — "
        "when they decide, you will automatically receive a system message to continue."
        f"{group_note}"
    )
