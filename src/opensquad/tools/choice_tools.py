"""Choice tools — let the agent ask the user to pick one of N proposed options.

Companion to ``agent_mode_tools.request_switch`` (which is a 2-way Approve/Deny).
This module provides an N-way choice card: the agent proposes several solution
plans / task options, the user picks one or more (or ignores), and the agent
is nudged to continue with the chosen plan.

Routing (mutually exclusive):
- **Group chat** (active group / ``group_id`` set and post succeeds): post a
  ``[[PROPOSE_OPTIONS]]`` card in the group only — do **not** also show the
  Agent Web inline card.
- **Private AI chat**: emit a ``propose_options`` bus event for the Agent Web
  card; resolve via ``resolve_proposed_options`` WS command.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_MAX_OPTIONS = 12
_MAX_TITLE = 120
_MAX_DESC = 600
_SPLIT_RE = re.compile(r"[\n,;|]+")


def _maybe_parse_structured(value: Any) -> Any:
    """Best-effort parse of JSON / Python-literal strings from tool callers."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text[0] in "[{":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(text)
            except Exception:
                return value
    return value


def coerce_options_arg(options: Any) -> list[Any] | None:
    """
    Normalize messy tool-call shapes into a flat option list.

    Accepts:
    - list[str] / list[dict]
    - JSON/Python string of the above
    - ``{"options": [...]}`` / ``{"items": [...]}`` / ``{"choices": [...]}``
    - newline / comma separated titles
    """
    raw = _maybe_parse_structured(options)
    if isinstance(raw, dict):
        for key in ("options", "items", "choices", "list", "data"):
            inner = raw.get(key)
            if isinstance(inner, list):
                raw = inner
                break
            if isinstance(inner, str):
                parsed_inner = _maybe_parse_structured(inner)
                if isinstance(parsed_inner, list):
                    raw = parsed_inner
                    break
        else:
            if any(raw.get(k) for k in ("title", "name", "label", "text", "content", "value")):
                raw = [raw]
            else:
                return None

    if isinstance(raw, str):
        parts = [p.strip() for p in _SPLIT_RE.split(raw) if p.strip()]
        raw = parts

    if not isinstance(raw, list):
        return None
    return raw


def _normalize_option(raw: Any, idx: int) -> dict[str, str] | None:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        # Tolerate stringified option dicts inside a list.
        if text.startswith("{"):
            parsed = _maybe_parse_structured(text)
            if isinstance(parsed, dict):
                return _normalize_option(parsed, idx)
        return {"id": f"opt_{idx + 1}", "title": text[:_MAX_TITLE], "description": ""}
    if isinstance(raw, dict):
        oid = str(
            raw.get("id") or raw.get("value") or raw.get("key") or raw.get("option_id") or f"opt_{idx + 1}"
        ).strip()
        title = str(
            raw.get("title")
            or raw.get("name")
            or raw.get("label")
            or raw.get("text")
            or raw.get("content")
            or raw.get("option")
            or ""
        ).strip()
        if not title and oid and not oid.startswith("opt_"):
            # {"value": "search"} → use value as both id and title
            title = oid
        if not title:
            return None
        desc = str(
            raw.get("description")
            or raw.get("summary")
            or raw.get("desc")
            or raw.get("detail")
            or raw.get("hint")
            or ""
        ).strip()
        return {
            "id": oid[:80] or f"opt_{idx + 1}",
            "title": title[:_MAX_TITLE],
            "description": desc[:_MAX_DESC],
        }
    return None


def normalize_options_list(options: Any) -> list[dict[str, str]]:
    """Coerce + normalize options; returns [] when nothing usable is found."""
    raw_list = coerce_options_arg(options)
    if not raw_list:
        return []
    norm: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for idx, raw in enumerate(raw_list[:_MAX_OPTIONS]):
        opt = _normalize_option(raw, idx)
        if not opt:
            continue
        if opt["id"] in seen_ids:
            opt["id"] = f"opt_{idx + 1}"
        seen_ids.add(opt["id"])
        norm.append(opt)
    return norm


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "multi", "multiple"}
    return default


async def propose_options(
    prompt: str,
    options: Any = None,
    *,
    group_id: str = "",
    allow_custom: bool = True,
    allow_multiple: bool = False,
    multi: bool | None = None,
) -> str:
    """Ask the user to pick one or more proposed options / solution plans.

    Use this when you have multiple viable approaches and the user should
    decide which one(s) to pursue.

    Emits an interactive choice card in **one** place only: the group when
    the turn is in a group, otherwise the private Agent Web UI.
    **Stop this turn** after calling it; when the user picks (or ignores),
    you will automatically receive a system message with their choice.

    Args:
        prompt: Question / instruction at the top of the card.
        options: 2–12 options. Tolerant of common tool-call shapes:
            list of strings, list of ``{id,title,description}`` /
            ``{label,value}`` dicts, JSON strings of those, or
            ``{"options":[...]}`` wrappers.
        group_id: Optional group id/name. Empty → use active group if any.
        allow_custom: Show "输入自己的答案" free-form option (default True).
        allow_multiple: When True, user may select multiple listed options.
        multi: Alias of ``allow_multiple`` (for shorter tool calls).
    """
    from opensquad.events import bus

    multi_select = _coerce_bool(allow_multiple, False) or _coerce_bool(multi, False)
    norm = normalize_options_list(options)
    if len(norm) < 2:
        return (
            "propose_options needs at least 2 valid options. "
            'Pass a list like ["A", "B"] or '
            "[{id,title,description}, ...] "
            "(JSON strings and {options:[...]} wrappers are also accepted)."
        )

    req_id = f"opt_{uuid.uuid4().hex[:14]}"
    prompt_text = (prompt or "").strip() or ("请选择一个或多个选项：" if multi_select else "请选择一个选项：")

    # Prefer group card when talking in a group — exclusive with Agent Web card.
    group_posted = False
    group_note = ""
    posted_group_id = ""
    try:
        from opensquad.collab_approval import (
            post_group_propose_options_card,
            resolve_agent_identity,
            resolve_current_group_id,
        )

        gid = resolve_current_group_id(group_id)
        if gid:
            agent_id, agent_name = resolve_agent_identity()
            result = post_group_propose_options_card(
                {
                    "id": req_id,
                    "prompt": prompt_text,
                    "options": norm,
                    "allow_custom": bool(allow_custom),
                    "allow_multiple": multi_select,
                    "status": "pending",
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "group_id": gid,
                },
                gid,
            )
            if isinstance(result, dict) and result.get("ok"):
                group_posted = True
                posted_group_id = str(result.get("group_id") or gid)
                group_note = (
                    f" A choice card was posted to group {posted_group_id} only "
                    "(not shown in Agent Web). Wait for the user to pick there."
                )
            elif isinstance(result, dict):
                logger.warning("[choice] group propose_options card failed: %s", result.get("error"))
    except Exception as e:
        logger.warning("[choice] group propose_options path failed: %s", e)

    if not group_posted:
        payload = {
            "event": "propose_options",
            "id": req_id,
            "prompt": prompt_text,
            "options": norm,
            "allow_custom": bool(allow_custom),
            "allow_multiple": multi_select,
            "status": "pending",
            "text": f"Propose options: {prompt_text}",
        }
        try:
            await bus.emit_async("info", payload)
            try:
                from opensquad import session_manager as _sm_mod

                _sm_mod.session_manager.add_event("info", payload)
            except Exception as persist_err:
                logger.debug("[choice] propose_options session persist skipped: %s", persist_err)
        except Exception as e:
            logger.warning("[choice] Failed to emit propose_options event: %s", e)
            return f"Failed to propose options: {e}"

    wait_where = (
        "Waiting for the user to pick in the **group chat card**."
        if group_posted
        else "Waiting for the user to pick in the Agent Web chat UI."
    )
    mode_note = " (multi-select enabled)" if multi_select else ""
    option_list = "; ".join(f"{i + 1}. {o['title']}" for i, o in enumerate(norm))
    pick_word = "one or more options" if multi_select else "one option"
    return (
        f"Options proposed{mode_note}: {prompt_text} [{option_list}]. "
        f"{wait_where} "
        f"Stop this turn after presenting the options — when the user picks {pick_word} "
        "(or types their own answer), you will automatically receive a system "
        "message with their choice to continue."
        f"{group_note}"
    )
