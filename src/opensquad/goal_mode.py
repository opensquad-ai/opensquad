"""Codex-style /goal mode — persistent objective with pursue / pause / resume / clear.

Goal is orthogonal to Plan/Build agent_mode: it is a session-level completion
contract with optional idle continuation while status == pursuing.
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

STATUS_PURSUING = "pursuing"
STATUS_PAUSED = "paused"
STATUS_ACHIEVED = "achieved"
STATUS_CLEARED = "cleared"

ACTIVE_STATUSES = frozenset({STATUS_PURSUING, STATUS_PAUSED, STATUS_ACHIEVED})
VALID_STATUSES = frozenset({STATUS_PURSUING, STATUS_PAUSED, STATUS_ACHIEVED, STATUS_CLEARED})

_USER_GOAL_RE = re.compile(
    r"<user_goal>\s*([\s\S]*?)\s*</user_goal>",
    re.IGNORECASE,
)
_USER_GOAL_CMD_RE = re.compile(
    r"<user_goal_cmd>\s*(pause|resume|clear|status)\s*</user_goal_cmd>",
    re.IGNORECASE,
)

# In-memory state (synced to session_data["goal"])
_goal: dict[str, Any] | None = None
_continuation_armed: bool = True  # reset when entering a wait cycle


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_goal() -> dict[str, Any]:
    return {
        "objective": "",
        "status": STATUS_CLEARED,
        "updated_at": _utc_now(),
        "last_progress": "",
        "blocked_reason": "",
    }


def get_goal() -> dict[str, Any] | None:
    """Return a copy of the active goal, or None if cleared/absent."""
    global _goal
    if not _goal:
        return None
    if _goal.get("status") == STATUS_CLEARED or not str(_goal.get("objective") or "").strip():
        return None
    return deepcopy(_goal)


def get_goal_raw() -> dict[str, Any]:
    return deepcopy(_goal) if _goal else _empty_goal()


def is_pursuing() -> bool:
    g = get_goal()
    return bool(g and g.get("status") == STATUS_PURSUING)


def arm_continuation() -> None:
    """Allow one continuation nudge for the next idle wait cycle."""
    global _continuation_armed
    _continuation_armed = True


def _persist(state: dict[str, Any] | None) -> None:
    try:
        from opensquad.runner import _get_session_manager

        sm = _get_session_manager()
        if state is None or state.get("status") == STATUS_CLEARED:
            sm.session_data.pop("goal", None)
        else:
            sm.session_data["goal"] = deepcopy(state)
        if hasattr(sm, "_save_session"):
            sm._save_session()
    except Exception as e:
        logger.debug("[goal_mode] persist skipped: %s", e)


def load_goal_from_session(session_data: dict | None = None) -> dict[str, Any] | None:
    """Load goal from session_data into memory (call on session load / boot)."""
    global _goal
    try:
        data = session_data
        if data is None:
            from opensquad.runner import _get_session_manager

            data = _get_session_manager().session_data or {}
        raw = (data or {}).get("goal")
        if not isinstance(raw, dict) or not str(raw.get("objective") or "").strip():
            _goal = None
            return None
        status = str(raw.get("status") or STATUS_CLEARED).strip().lower()
        if status not in VALID_STATUSES or status == STATUS_CLEARED:
            _goal = None
            return None
        _goal = {
            "objective": str(raw.get("objective") or "").strip(),
            "status": status,
            "updated_at": str(raw.get("updated_at") or _utc_now()),
            "last_progress": str(raw.get("last_progress") or ""),
            "blocked_reason": str(raw.get("blocked_reason") or ""),
        }
        return get_goal()
    except Exception as e:
        logger.warning("[goal_mode] load_goal_from_session failed: %s", e)
        _goal = None
        return None


def clear_goal_memory() -> None:
    """Clear in-memory goal (e.g. on new_session) without requiring persist."""
    global _goal
    _goal = None


async def notify_goal_changed(state: dict[str, Any] | None = None, *, text: str = "") -> None:
    """Emit goal_changed info event for UI sync."""
    g = state if state is not None else get_goal()
    try:
        from opensquad.events import bus

        payload: dict[str, Any] = {
            "event": "goal_changed",
            "goal": deepcopy(g) if g else None,
            "text": text or (f"Goal {(g or {}).get('status', 'cleared')}" if g else "Goal cleared"),
        }
        await bus.emit_async("info", payload)
    except Exception as e:
        logger.warning("[goal_mode] emit goal_changed failed: %s", e)


# Back-compat alias used internally
_emit_changed = notify_goal_changed


def set_goal(objective: str) -> dict[str, Any]:
    """Start or replace a pursuing goal."""
    global _goal
    obj = (objective or "").strip()
    if not obj:
        return {"ok": False, "error": "objective required"}
    _goal = {
        "objective": obj,
        "status": STATUS_PURSUING,
        "updated_at": _utc_now(),
        "last_progress": "",
        "blocked_reason": "",
    }
    arm_continuation()
    _persist(_goal)
    return {"ok": True, "goal": get_goal()}


def pause_goal() -> dict[str, Any]:
    global _goal
    if not _goal or _goal.get("status") not in (STATUS_PURSUING, STATUS_PAUSED):
        return {"ok": False, "error": "no active goal to pause"}
    _goal["status"] = STATUS_PAUSED
    _goal["updated_at"] = _utc_now()
    _persist(_goal)
    return {"ok": True, "goal": get_goal()}


def resume_goal() -> dict[str, Any]:
    global _goal
    if not _goal or not str(_goal.get("objective") or "").strip():
        return {"ok": False, "error": "no goal to resume"}
    if _goal.get("status") == STATUS_ACHIEVED:
        return {"ok": False, "error": "goal already achieved; set a new goal"}
    _goal["status"] = STATUS_PURSUING
    _goal["updated_at"] = _utc_now()
    _goal["blocked_reason"] = ""
    arm_continuation()
    _persist(_goal)
    return {"ok": True, "goal": get_goal()}


def clear_goal() -> dict[str, Any]:
    global _goal
    _goal = None
    _persist(None)
    return {"ok": True, "goal": None}


def update_progress(note: str) -> dict[str, Any]:
    global _goal
    if not _goal or _goal.get("status") not in (STATUS_PURSUING, STATUS_PAUSED):
        return {"ok": False, "error": "no active goal"}
    _goal["last_progress"] = (note or "").strip()
    _goal["updated_at"] = _utc_now()
    _persist(_goal)
    return {"ok": True, "goal": get_goal()}


def mark_achieved(evidence: str = "") -> dict[str, Any]:
    global _goal
    if not _goal or _goal.get("status") != STATUS_PURSUING:
        return {"ok": False, "error": "goal must be pursuing to mark achieved"}
    ev = (evidence or "").strip()
    if not ev:
        return {"ok": False, "error": "evidence required — describe how you verified the goal"}
    _goal["status"] = STATUS_ACHIEVED
    _goal["last_progress"] = ev
    _goal["updated_at"] = _utc_now()
    _goal["blocked_reason"] = ""
    _persist(_goal)
    return {"ok": True, "goal": get_goal()}


def report_blocked(reason: str) -> dict[str, Any]:
    global _goal
    if not _goal or _goal.get("status") != STATUS_PURSUING:
        return {"ok": False, "error": "no pursuing goal"}
    _goal["blocked_reason"] = (reason or "").strip()
    _goal["updated_at"] = _utc_now()
    _persist(_goal)
    return {"ok": True, "goal": get_goal()}


async def apply_goal_action(
    action: str,
    *,
    objective: str = "",
    nudge: bool = True,
) -> dict[str, Any]:
    """Apply set/pause/resume/clear/status from WS or tools; emit goal_changed."""
    act = (action or "").strip().lower()
    result: dict[str, Any]

    if act in ("set", "start"):
        result = set_goal(objective)
        text = f"Goal set: {objective.strip()[:120]}"
    elif act == "pause":
        result = pause_goal()
        text = "Goal paused"
    elif act == "resume":
        result = resume_goal()
        text = "Goal resumed"
    elif act == "clear":
        result = clear_goal()
        text = "Goal cleared"
    elif act == "status":
        g = get_goal()
        result = {"ok": True, "goal": g}
        text = f"Goal status: {(g or {}).get('status', 'none')}"
    else:
        return {"ok": False, "error": f"unknown action: {action}"}

    if not result.get("ok"):
        return result

    await _emit_changed(result.get("goal"), text=text)

    if nudge and act == "resume" and result.get("goal"):
        try:
            from opensquad.input_hub import input_hub

            input_hub.push(
                goal_continuation_message(result["goal"]),
                source="system",
            )
        except Exception as e:
            logger.debug("[goal_mode] resume nudge failed: %s", e)

    if nudge and act in ("set", "start") and result.get("goal"):
        # Kickoff is normally the user chat with <user_goal>; WS-only set still nudges.
        pass

    return result


def goal_prompt_section(state: dict[str, Any] | None = None) -> str:
    """Dynamic system-prompt section when a goal is active."""
    g = state if state is not None else get_goal()
    if not g:
        return ""
    status = g.get("status") or STATUS_PURSUING
    obj = g.get("objective") or ""
    progress = (g.get("last_progress") or "").strip()
    blocked = (g.get("blocked_reason") or "").strip()

    lines = [
        "## Active Goal (/goal mode)",
        "",
        f"**Status:** `{status}`",
        f"**Objective:** {obj}",
    ]
    if progress:
        lines.append(f"**Last progress:** {progress}")
    if blocked:
        lines.append(f"**Blocked:** {blocked}")

    if status == STATUS_PURSUING:
        lines.extend(
            [
                "",
                "You are in a goal-execute-verify loop. Keep working until the objective is",
                "verifiably met, then call `goal__mark_achieved` with concrete evidence.",
                "Prefer Build mode for edits/commands. Use `<plan>` and optional `GOAL_PLAN.md`",
                "as external memory. `system.wait` / ending a turn does NOT mean the goal is done —",
                "the runtime may continue you automatically.",
                "If paused externally, stop mutating work for this goal.",
            ]
        )
    elif status == STATUS_PAUSED:
        lines.extend(
            [
                "",
                "This goal is **paused**. Do not continue implementing it until the user",
                "runs `/goal resume`. You may answer unrelated questions normally.",
            ]
        )
    elif status == STATUS_ACHIEVED:
        lines.extend(
            [
                "",
                "This goal was marked **achieved**. Do not reopen it unless the user sets a new `/goal`.",
            ]
        )

    return "\n".join(lines)


def goal_continuation_message(state: dict[str, Any] | None = None) -> str:
    g = state if state is not None else get_goal()
    obj = (g or {}).get("objective") or "(unknown)"
    progress = ((g or {}).get("last_progress") or "").strip()
    blocked = ((g or {}).get("blocked_reason") or "").strip()
    extra = ""
    if progress:
        extra += f"\nLast progress note: {progress}"
    if blocked:
        extra += f"\nPreviously reported blocker: {blocked}"
    return (
        "[System — Goal continuation] An active `/goal` is still pursuing.\n"
        f"Objective: {obj}{extra}\n"
        "Review evidence against the objective. If met, call `goal__mark_achieved` with verification details. "
        "Otherwise continue the next concrete step (update `<plan>` / GOAL_PLAN.md as needed). "
        "Do not ask the user whether to continue — proceed autonomously unless blocked."
    )


def should_continue_goal() -> bool:
    """True when idle wait may inject one continuation."""
    if not is_pursuing():
        return False
    return _continuation_armed


def take_continuation() -> str | None:
    """If pursuing and armed, disarm and return continuation message."""
    global _continuation_armed
    if not should_continue_goal():
        return None
    _continuation_armed = False
    return goal_continuation_message()


def expand_user_goal(user_text: str) -> str:
    """Expand <user_goal> for the LLM turn; apply set_goal as side effect."""
    if not user_text or "<user_goal>" not in user_text.lower():
        return user_text

    match = _USER_GOAL_RE.search(user_text)
    if not match:
        return user_text

    objective = (match.group(1) or "").strip()
    remainder = (user_text[: match.start()] + user_text[match.end() :]).strip()
    if objective:
        set_goal(objective)

    parts = [
        "[User set a long-running /goal — enter goal-execute-verify mode]",
        f"**Objective:** {objective or '(empty)'}",
        "",
        "Decompose into a verifiable plan, execute, and verify. Keep iterating until the",
        "objective is met, then call `goal__mark_achieved` with evidence. Prefer writing",
        "progress to GOAL_PLAN.md / `<plan>` so you do not lose track across turns.",
        "Do not stop after a single step unless the goal is fully verified.",
    ]
    if remainder:
        parts.extend(["", "[Additional user notes]", remainder])
    return "\n".join(parts)


def try_handle_goal_cmd_tag(user_text: str) -> tuple[str | None, dict[str, Any] | None]:
    """If message is only a goal cmd tag, return (display, action result pending async).

    Sync helper — returns action name for caller to await apply_goal_action.
    """
    if not user_text or "<user_goal_cmd>" not in user_text.lower():
        return None, None
    m = _USER_GOAL_CMD_RE.search(user_text)
    if not m:
        return None, None
    action = m.group(1).lower()
    return action, {"action": action}


def parse_slash_goal_line(text: str) -> dict[str, Any] | None:
    """Parse a leading `/goal …` line (for tests / CLI). Returns action dict or None."""
    m = re.match(r"^\s*/goal(?:\s+(.*))?$", text or "", re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    rest = (m.group(1) or "").strip()
    if not rest:
        return {"action": "status"}
    low = rest.lower()
    if low in ("pause", "resume", "clear", "status"):
        return {"action": low}
    return {"action": "set", "objective": rest}
