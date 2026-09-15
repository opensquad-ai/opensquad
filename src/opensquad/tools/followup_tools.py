"""Follow-up suggestion tool — let the agent offer tappable next-turn prompts.

Companion to ``choice_tools.propose_options`` (a blocking N-way decision card).
This one is **non-blocking**: the agent calls it at the very end of its tool
flow, right before the final answer. The Agent Web UI renders 1–3 quick replies
directly under that final answer; tapping one sends its text verbatim as the
user's next message. Nothing is awaited — the turn ends normally.

Routing: Agent Web only. Group chats / TUI / scheduled tasks keep working
untouched, because a follow-up chip is a UI affordance (an *offer*), not a
decision the agent blocks on. There is deliberately no group-card fallback.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 3
# Split on newlines / semicolons / pipes only — never on commas, because a
# natural follow-up question ("分析板块与十五五规划的关联，给出证据") contains them.
_SPLIT_RE = re.compile(r"[\n;|]+")
_MAX_TEXT = 200


def _coerce_suggestion_list(value: Any) -> list[str]:
    """Normalize messy tool-call shapes into a flat list of suggestion strings.

    Accepts list[str], list[dict] (``{text|title|content|label}``), a JSON string
    of either, ``{"suggestions": [...]}`` / ``{"options": [...]}`` wrappers, and
    newline / semicolon separated plain text.
    """
    raw: Any = value
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text[0] in "[{":
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                raw = [text]
        else:
            raw = [p.strip() for p in _SPLIT_RE.split(text) if p.strip()]

    if isinstance(raw, dict):
        for key in ("suggestions", "options", "followups", "follow_ups", "items", "list"):
            inner = raw.get(key)
            if isinstance(inner, list):
                raw = inner
                break
            if isinstance(inner, str):
                try:
                    parsed = json.loads(inner)
                except json.JSONDecodeError:
                    parsed = [p.strip() for p in _SPLIT_RE.split(inner) if p.strip()]
                if isinstance(parsed, list):
                    raw = parsed
                    break
        else:
            single = raw.get("text") or raw.get("title") or raw.get("content") or raw.get("label")
            raw = [single] if single else []

    if isinstance(raw, str):
        raw = [p.strip() for p in _SPLIT_RE.split(raw) if p.strip()]
    if not isinstance(raw, list):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            s = item.strip()
        elif isinstance(item, dict):
            s = str(
                item.get("text")
                or item.get("title")
                or item.get("content")
                or item.get("label")
                or item.get("question")
                or ""
            ).strip()
        else:
            s = ""
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s[:_MAX_TEXT])
    return out


async def suggest_followups(suggestions: list[str] | None = None, text: str = "") -> str:
    """Offer 1–3 likely follow-up questions the user may want to send next.

    对话后续预期：Call this **once, at the very end of your tool flow, right
    before you write the final answer to the user**. The Agent Web UI renders
    the suggestions as light-theme chips under your final answer; tapping one
    sends it verbatim as the user's next message.

    This is an *offer*, not a question: it does NOT block. Always finish your
    answer normally afterwards — never wait for the user to pick a chip.

    Args:
        suggestions: An **array of strings** (1–3 items), phrased from the
            user's perspective as short self-contained questions/instructions
            (e.g. ``["把这次复盘的结论导出成 md 报告", "再跑一次全量测试"]``).
            Runtime is tolerant of looser shapes too — a JSON string, a list of
            ``{text}``/``{title}`` dicts, a ``{"suggestions":[...]}`` wrapper, or
            newline/semicolon-separated text — but the schema advertises an
            array, so prefer passing one.
        text: Optional single suggestion, used when ``suggestions`` is omitted.
    """
    items = _coerce_suggestion_list(suggestions)
    if not items and text:
        items = _coerce_suggestion_list(text)
    if not items:
        return (
            "suggest_followups needs 1–3 suggestions. Pass a list like "
            '["问题一", "问题二"] (JSON strings and {suggestions:[...]} wrappers '
            "are also accepted)."
        )
    items = items[:MAX_SUGGESTIONS]

    req_id = f"fu_{uuid.uuid4().hex[:14]}"
    payload = {
        "event": "suggest_followups",
        "id": req_id,
        "suggestions": [{"id": f"fu_{i + 1}", "text": s} for i, s in enumerate(items)],
        "text": "建议追问：" + " / ".join(items),
    }

    from opensquad.events import bus

    try:
        await bus.emit_async("info", payload)
        try:
            from opensquad import session_manager as _sm_mod

            _sm_mod.session_manager.add_event("info", payload)
        except Exception as persist_err:
            logger.debug("[followup] session persist skipped: %s", persist_err)
    except Exception as e:
        logger.warning("[followup] Failed to emit suggest_followups event: %s", e)
        return f"Failed to offer follow-up suggestions: {e}"

    listed = "; ".join(f"{i + 1}. {s}" for i, s in enumerate(items))
    return (
        f"Follow-up suggestions offered to the user: [{listed}]. "
        "They are now shown as tappable chips under your final answer. "
        "This is an offer only — do NOT wait for a reply; finish your answer normally."
    )
