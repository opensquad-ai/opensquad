"""Choice tools — let the agent ask the user to pick one of N proposed options.

Companion to ``agent_mode_tools.request_switch`` (which is a 2-way Approve/Deny).
This module provides an N-way single-choice card: the agent proposes several
solution plans / task options, the user picks one (or ignores), and the agent
is nudged to continue with the chosen plan.

- In **private AI chat**: a ``propose_options`` bus event renders an inline
  card above the composer (web UI). The user's choice comes back via the
  ``resolve_proposed_options`` WS command.
- In **group chat** (or when ``group_id`` is set): also posts a choice card
  in the group via the ``[[PROPOSE_OPTIONS]]`` marker, resolved via REST.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_MAX_OPTIONS = 12
_MAX_TITLE = 120
_MAX_DESC = 600


def _normalize_option(raw: Any, idx: int) -> dict[str, str] | None:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        return {"id": f"opt_{idx + 1}", "title": text[:_MAX_TITLE], "description": ""}
    if isinstance(raw, dict):
        oid = str(raw.get("id") or f"opt_{idx + 1}").strip()
        title = str(raw.get("title") or raw.get("name") or "").strip()
        if not title:
            return None
        desc = str(raw.get("description") or raw.get("summary") or raw.get("desc") or "").strip()
        return {
            "id": oid[:80] or f"opt_{idx + 1}",
            "title": title[:_MAX_TITLE],
            "description": desc[:_MAX_DESC],
        }
    return None


async def propose_options(
    prompt: str,
    options: list[dict[str, str]] | list[str],
    *,
    group_id: str = "",
    allow_custom: bool = True,
) -> str:
    """Ask the user to pick one of several proposed options / solution plans.

    Use this when you have multiple viable approaches and the user should
    decide which one to pursue — e.g. "browse project structure" vs "code
    search" vs "run git status", or different fix strategies.

    Emits an interactive single-choice card. **Stop this turn** after calling
    it; when the user picks an option (or ignores), you will automatically
    receive a system message telling you which option was chosen so you can
    continue.

    Args:
        prompt: The question / instruction shown at the top of the card
            (e.g. "请选择一个任务来测试系统功能：").
        options: 2–12 options. Each item may be a string (title only) or a
            dict with ``id`` / ``title`` / ``description``. ``description``
            is shown as smaller grey text under the title.
        group_id: Optional group id/name. If empty and the current turn is
            from a group, the active group is used automatically and a card
            is posted there too.
        allow_custom: When True (default), the card also shows an
            "输入自己的答案" option so the user can type a free-form answer.
    """
    from opensquad.events import bus

    raw_options = options or []
    if not isinstance(raw_options, list):
        return "Invalid options: expected a list of strings or {id,title,description} dicts."
    norm: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for idx, raw in enumerate(raw_options[:_MAX_OPTIONS]):
        opt = _normalize_option(raw, idx)
        if not opt:
            continue
        if opt["id"] in seen_ids:
            opt["id"] = f"opt_{idx + 1}"
        seen_ids.add(opt["id"])
        norm.append(opt)
    if len(norm) < 2:
        return "propose_options needs at least 2 valid options. Provide a list of titles or {title, description} dicts."

    req_id = f"opt_{uuid.uuid4().hex[:14]}"
    prompt_text = (prompt or "").strip() or "请选择一个选项："

    payload = {
        "event": "propose_options",
        "id": req_id,
        "prompt": prompt_text,
        "options": norm,
        "allow_custom": bool(allow_custom),
        "status": "pending",
        "text": f"Propose options: {prompt_text}",
    }

    # Always emit bus event so private AI-chat UI can show a card too
    try:
        await bus.emit_async("info", payload)
    except Exception as e:
        logger.warning("[choice] Failed to emit propose_options event: %s", e)
        return f"Failed to propose options: {e}"

    # Prefer group card when talking in a group
    group_posted = False
    group_note = ""
    try:
        from opensquad.collab_approval import resolve_current_group_id

        gid = resolve_current_group_id(group_id)
        if gid:
            try:
                from opensquad.collab_approval import post_group_propose_options_card

                result = post_group_propose_options_card(
                    {
                        "id": req_id,
                        "prompt": prompt_text,
                        "options": norm,
                        "allow_custom": bool(allow_custom),
                        "status": "pending",
                    },
                    gid,
                )
                if isinstance(result, dict) and result.get("ok"):
                    group_posted = True
                    group_note = (
                        f" A choice card was also posted to group {result.get('group_id') or gid}. "
                        "Prefer picking there when chatting in the group."
                    )
            except Exception as e:
                logger.warning("[choice] group propose_options path failed: %s", e)
    except Exception as e:
        logger.warning("[choice] group resolution failed: %s", e)

    wait_where = (
        "Waiting for the user to pick an option in the **group chat card** (or the AI chat UI)."
        if group_posted
        else "Waiting for the user to pick an option in the chat UI."
    )
    option_list = "; ".join(f"{i + 1}. {o['title']}" for i, o in enumerate(norm))
    return (
        f"Options proposed: {prompt_text} [{option_list}]. "
        f"{wait_where} "
        "Stop this turn after presenting the options — when the user picks one "
        "(or types their own answer), you will automatically receive a system "
        "message with their choice to continue."
        f"{group_note}"
    )
