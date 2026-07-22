"""Cursor-style /plan workflow — expand <user_plan> and ensure Plan mode."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_USER_PLAN_RE = re.compile(
    r"<user_plan>\s*([\s\S]*?)\s*</user_plan>",
    re.IGNORECASE,
)

PLAN_DOC_DIR = ".opensquad/plans"


def suggested_plan_path(topic: str = "") -> str:
    """Suggest a Markdown plan path under `.opensquad/plans/`."""
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    slug = re.sub(r"[^a-z0-9]+", "-", (topic or "plan").lower()).strip("-")[:48] or "plan"
    return f"{PLAN_DOC_DIR}/{day}-{slug}.md"


def expand_user_plan(user_text: str) -> str:
    """Expand <user_plan>…</user_plan> into a Cursor-style planning kickoff."""
    if not user_text or "<user_plan>" not in user_text.lower():
        return user_text

    match = _USER_PLAN_RE.search(user_text)
    if not match:
        return user_text

    topic = (match.group(1) or "").strip()
    remainder = (user_text[: match.start()] + user_text[match.end() :]).strip()
    path_hint = suggested_plan_path(topic or "plan")

    parts = [
        "[User started Cursor-style /plan — design before coding]",
        f"**Topic:** {topic or '(open-ended — clarify the goal first)'}",
        f"**Plan document (create/update):** `{path_hint}`",
        "",
        "Follow this workflow strictly:",
        "1. **Ensure Plan mode** — if not already in Plan, call "
        '`agent_mode__request_switch` with `target_mode="plan"` only if needed; '
        "when the user invoked `/plan`, prefer assuming Plan is (or will be) active.",
        "2. **Investigate** — read/search the codebase; map files, dependencies, risks.",
        "3. **Clarify** — if the ask is vague, ask focused questions OR use "
        "`choice_tools__propose_options`. Do not invent a large scope without alignment.",
        "4. **Write an editable Markdown plan** under `.opensquad/plans/` covering:",
        "   - Goal & non-goals / scope",
        "   - Architecture / approach (diagrams in mermaid when helpful)",
        "   - Files to create/modify",
        "   - Step-by-step implementation todos",
        "   - Risks, trade-offs, test plan",
        "5. **Emit `<plan>`** checklist matching the MD steps (`[ ]` / `[>]` / `[x]`).",
        "6. **Request Build** — call `agent_mode__request_switch` with "
        '`target_mode="build"` and mention the plan file path; STOP and wait for approval.',
        "7. After Build is approved, implement **from the Markdown plan**, updating `<plan>` as you go.",
        "",
        "Do NOT edit application source outside `.opensquad/plans/` while still in Plan mode.",
    ]
    if remainder:
        parts.extend(["", "[Additional user notes]", remainder])
    return "\n".join(parts)


async def ensure_plan_mode_for_user_plan() -> None:
    """Switch runner into Plan mode when user sends /plan (no approval needed)."""
    try:
        from opensquad.agent_mode import MODE_PLAN, get_current_mode, set_current_mode
        from opensquad.events import bus
        from opensquad.model_switch import apply_agent_mode

        if get_current_mode() == MODE_PLAN:
            set_current_mode(MODE_PLAN)
            return
        result = await apply_agent_mode(MODE_PLAN, approved_request_id=None)
        if not result.get("ok"):
            set_current_mode(MODE_PLAN)
            await bus.emit_async(
                "info",
                {
                    "event": "agent_mode_changed",
                    "mode": MODE_PLAN,
                    "text": "Agent mode set to plan (/plan)",
                },
            )
    except Exception as e:
        logger.warning("[plan_workflow] ensure_plan_mode failed: %s", e)
        try:
            from opensquad.agent_mode import MODE_PLAN, set_current_mode

            set_current_mode(MODE_PLAN)
        except Exception:
            pass
