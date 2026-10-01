"""Generic window cards — `[[WINDOW_CARD]]{json}[[/WINDOW_CARD]]`.

A window card is the *generic* form of the interactive cards: a chat message
that renders as a small card in group chat **or** in a DM, and opens a window
whose content is described entirely by the payload. The window's content is
whatever the sender put in ``view`` — sections, a table, a flow/steps list,
metrics or raw text — so a new use case needs no new component and no new
endpoint.

Like the approval and collaboration cards this rides on TEXT content (a marker
plus a readable fallback), so nothing has to be migrated and the agent on the
other side still reads plain text.

Payload::

    {
      "v": 1, "id": "wcard_ab12cd34ef56",
      "kind": "info",                # card flavour: info|table|flow|task|report
      "title": "...", "summary": "...", "icon": "table",
      "sender": {"agent_id": "...", "agent_name": "..."},
      "target": {"group_id": "...", "recipient_name": "..."},
      "source": "",                  # optional refetch hint for the window
      "view": {
        "kind": "sections|table|flow|metrics|raw",
        "blocks": [{"title": "", "text": "", "items": [], "status": ""}],
        "columns": ["..."], "rows": [["..."]],
        "steps": [{"title": "", "detail": "", "status": ""}],
        "items": [{"label": "", "value": ""}],
        "text": ""
      },
      "actions": [{"id": "...", "label": "...", "intent": "open_url|copy|open_collab_task|none",
                   "url": "", "copy": "", "collab_id": ""}],
      "state": "open"
    }
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


WINDOW_CARD_START = "[[WINDOW_CARD]]"
WINDOW_CARD_END = "[[/WINDOW_CARD]]"

_WINDOW_CARD_RE = re.compile(
    r"\[\[WINDOW_CARD\]\]\s*(\{.*?\})\s*\[\[/WINDOW_CARD\]\]",
    re.DOTALL,
)

VIEW_KINDS = ("sections", "table", "flow", "metrics", "raw")

_VIEW_ALIASES = {
    "section": "sections",
    "list": "sections",
    "steps": "flow",
    "step": "flow",
    "process": "flow",
    "progress": "flow",
    "kv": "metrics",
    "metric": "metrics",
    "stats": "metrics",
    "cards": "metrics",
    "text": "raw",
    "markdown": "raw",
    "md": "raw",
}

VALID_INTENTS = ("open_url", "copy", "open_collab_task", "respond", "confirm", "decline", "none")

# Interactive fields a card may ask the user to fill in. Submitting a form (or
# pressing a respond/confirm/decline button) posts the answer back through the
# gateway, which rewrites the card's marker so every surface shows the same
# answered state and nudges the agent that sent it.
FORM_FIELD_TYPES = ("text", "textarea", "select", "radio", "checkbox")


def normalize_form(form: Any) -> dict[str, Any] | None:
    """Coerce a form definition into something the window can render."""
    if not isinstance(form, dict):
        return None
    fields: list[dict[str, Any]] = []
    for f in _as_list(form.get("fields")):
        if not isinstance(f, dict):
            continue
        fid = str(f.get("id") or f.get("name") or "").strip()
        if not fid:
            continue
        ftype = str(f.get("type") or "text").strip().lower()
        if ftype not in FORM_FIELD_TYPES:
            ftype = "text"
        options: list[dict[str, str]] = []
        for o in _as_list(f.get("options")):
            if isinstance(o, str):
                options.append({"id": o, "label": o})
            elif isinstance(o, dict):
                label = str(o.get("label") or o.get("value") or o.get("id") or "")
                options.append({"id": str(o.get("id") or o.get("value") or label), "label": label})
        fields.append(
            {
                "id": fid,
                "label": str(f.get("label") or fid),
                "type": ftype,
                "placeholder": str(f.get("placeholder") or ""),
                "required": bool(f.get("required")),
                "options": options,
            }
        )
    if not fields:
        return None
    return {
        "submit_label": str(form.get("submit_label") or "提交"),
        "cancel_label": str(form.get("cancel_label") or ""),
        "fields": fields,
    }


def new_window_card_id() -> str:
    return f"wcard_{uuid.uuid4().hex[:12]}"


def normalize_view_kind(kind: str) -> str:
    k = (kind or "").strip().lower()
    if k in VIEW_KINDS:
        return k
    return _VIEW_ALIASES.get(k, "sections")


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def normalize_view(view: Any) -> dict[str, Any]:
    """Coerce whatever the sender passed into a renderable view.

    Accepts a bare kind ("table"), or a dict with any of blocks / columns+rows /
    steps / items / text. Missing pieces stay empty so the renderer never needs
    to guess, and an unknown kind degrades to ``sections``.
    """
    if isinstance(view, str):
        view = {"kind": view}
    if not isinstance(view, dict):
        view = {}

    kind = str(view.get("kind") or "").strip()
    if not kind:
        if view.get("rows") or view.get("columns"):
            kind = "table"
        elif view.get("steps"):
            kind = "flow"
        elif view.get("items"):
            kind = "metrics"
        elif view.get("blocks"):
            kind = "sections"
        else:
            kind = "raw"
    kind = normalize_view_kind(kind)

    blocks: list[dict[str, Any]] = []
    for b in _as_list(view.get("blocks") or view.get("sections")):
        if isinstance(b, str):
            blocks.append({"title": "", "text": b, "items": [], "status": ""})
            continue
        if not isinstance(b, dict):
            continue
        blocks.append(
            {
                "title": str(b.get("title") or ""),
                "text": str(b.get("text") or b.get("content") or ""),
                "items": [str(i) for i in _as_list(b.get("items")) if isinstance(i, (str, int, float))],
                "status": str(b.get("status") or ""),
            }
        )

    steps: list[dict[str, str]] = []
    for s in _as_list(view.get("steps")):
        if isinstance(s, str):
            steps.append({"title": s, "detail": "", "status": ""})
        elif isinstance(s, dict):
            steps.append(
                {
                    "title": str(s.get("title") or s.get("name") or ""),
                    "detail": str(s.get("detail") or s.get("description") or ""),
                    "status": str(s.get("status") or ""),
                }
            )

    items: list[dict[str, str]] = []
    for it in _as_list(view.get("items") or view.get("metrics")):
        if isinstance(it, dict):
            items.append({"label": str(it.get("label") or it.get("name") or ""), "value": str(it.get("value") or "")})

    columns = [str(c) for c in _as_list(view.get("columns"))]
    rows: list[list[str]] = []
    for r in _as_list(view.get("rows")):
        if isinstance(r, dict):
            rows.append([str(r.get(c, "")) for c in columns] if columns else [str(v) for v in r.values()])
        elif isinstance(r, (list, tuple)):
            rows.append([str(c) for c in r])
        else:
            rows.append([str(r)])

    return {
        "kind": kind,
        "blocks": blocks,
        "steps": steps,
        "items": items,
        "columns": columns,
        "rows": rows,
        "form": normalize_form(view.get("form")),
        "text": str(view.get("text") or ""),
    }


def normalize_actions(actions: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for a in _as_list(actions):
        if isinstance(a, str):
            a = {"label": a}
        if not isinstance(a, dict):
            continue
        label = str(a.get("label") or a.get("title") or "").strip()
        if not label:
            continue
        intent = str(a.get("intent") or "").strip().lower()
        if intent not in VALID_INTENTS:
            if a.get("url"):
                intent = "open_url"
            elif a.get("collab_id"):
                intent = "open_collab_task"
            elif a.get("copy"):
                intent = "copy"
            else:
                intent = "none"
        out.append(
            {
                "id": str(a.get("id") or f"act_{len(out) + 1}"),
                "label": label,
                "intent": intent,
                "url": str(a.get("url") or ""),
                "copy": str(a.get("copy") or ""),
                "collab_id": str(a.get("collab_id") or ""),
            }
        )
    return out


def _view_with_form(view: Any, form: Any) -> dict[str, Any]:
    """Normalize the view and fold in an optional interactive form."""
    normalized = normalize_view(view)
    form_norm = normalize_form(form)
    if form_norm:
        normalized["form"] = form_norm
    return normalized


def build_window_card_payload(
    *,
    title: str,
    view: Any,
    summary: str = "",
    icon: str = "",
    kind: str = "info",
    group_id: str = "",
    recipient_name: str = "",
    actions: Any = None,
    sender_id: str = "",
    sender_name: str = "",
    form: Any = None,
    source: str = "",
    card_id: str = "",
    state: str = "open",
) -> dict[str, Any]:
    return {
        "v": 1,
        "id": card_id or new_window_card_id(),
        "kind": (kind or "info").strip().lower(),
        "title": (title or "").strip() or "卡片",
        "summary": (summary or "").strip(),
        "icon": (icon or "").strip(),
        "sender": {"agent_id": sender_id, "agent_name": sender_name or sender_id},
        "target": {"group_id": group_id, "recipient_name": recipient_name},
        "source": (source or "").strip(),
        "view": _view_with_form(view, form),
        "actions": normalize_actions(actions),
        "state": state,
    }


def encode_window_card_message(payload: dict[str, Any]) -> str:
    """Marker + readable fallback (the fallback is what non-UI clients see)."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    lines = [f"{WINDOW_CARD_START}{body}{WINDOW_CARD_END}", f"🪟 {payload.get('title') or '卡片'}"]
    summary = str(payload.get("summary") or "")
    if summary:
        lines.append(summary)
    view = payload.get("view") if isinstance(payload.get("view"), dict) else {}
    if view.get("kind") == "table":
        cols = view.get("columns") or []
        if cols:
            lines.append(" | ".join(str(c) for c in cols))
        for row in (view.get("rows") or [])[:5]:
            lines.append(" | ".join(str(c) for c in row))
    for a in payload.get("actions") or []:
        if isinstance(a, dict) and a.get("label"):
            lines.append(f"· {a['label']}")
    lines.append("（在客户端点击卡片可打开完整窗口）")
    return "\n".join(lines)


def parse_window_card_payload(content: str) -> dict[str, Any] | None:
    if not content or WINDOW_CARD_START not in content:
        return None
    m = _WINDOW_CARD_RE.search(content)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("id") or not data.get("title"):
        return None
    if not isinstance(data.get("view"), dict):
        return None
    data.setdefault("v", 1)
    data["view"] = normalize_view(data.get("view"))
    data["actions"] = normalize_actions(data.get("actions"))
    if not isinstance(data.get("sender"), dict):
        data["sender"] = {"agent_id": "", "agent_name": ""}
    if not isinstance(data.get("target"), dict):
        data["target"] = {"group_id": "", "recipient_name": ""}
    return data


def strip_window_card_marker(content: str) -> str:
    if not content or WINDOW_CARD_START not in content:
        return content
    return _WINDOW_CARD_RE.sub("", content).strip()


def patch_window_card_state_in_content(content: str, state: str, action_id: str = "") -> str:
    payload = parse_window_card_payload(content)
    if not payload:
        return content
    payload["state"] = state
    if action_id:
        payload["last_action_id"] = action_id
    marker = f"{WINDOW_CARD_START}{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}{WINDOW_CARD_END}"
    return _WINDOW_CARD_RE.sub(marker, content, count=1)


def patch_window_card_response_in_content(
    content: str,
    *,
    action_id: str = "",
    values: dict[str, Any] | None = None,
    by: str = "",
    state: str = "answered",
) -> str:
    """Record the user's answer on the card (state + response) in place."""
    payload = parse_window_card_payload(content)
    if not payload:
        return content
    payload["state"] = state
    if action_id:
        payload["last_action_id"] = action_id
    payload["response"] = {
        "action_id": action_id,
        "values": {str(k): v for k, v in (values or {}).items()},
        "by": by,
        "at": _now_iso(),
    }
    marker = f"{WINDOW_CARD_START}{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}{WINDOW_CARD_END}"
    return _WINDOW_CARD_RE.sub(marker, content, count=1)


def post_window_card(payload: dict[str, Any], *, group_id: str = "", recipient_name: str = "") -> dict[str, Any]:
    """Send a window card to a group or to a DM. Returns {ok, target_type, ...}."""
    from opensquad.bridge import bridge

    if not bridge or not bridge.token:
        return {"ok": False, "error": "Bridge not connected"}
    group_id = (group_id or "").strip()
    recipient_name = (recipient_name or "").strip()
    if not group_id and not recipient_name:
        return {"ok": False, "error": "group_id or recipient_name is required"}

    msg = encode_window_card_message(payload)
    if group_id:
        target = group_id
        try:
            groups = bridge.list_groups_api() or []
            if not any(isinstance(g, dict) and g.get("id") == group_id for g in groups):
                for g in groups:
                    if isinstance(g, dict) and g.get("name") == group_id:
                        target = str(g.get("id") or group_id)
                        break
        except Exception:
            pass
        ok = bridge.send_message(msg, target_id=target, target_type="group")
        if not ok:
            return {"ok": False, "error": "Failed to send window card", "group_id": target}
        return {
            "ok": True,
            "target_type": "group",
            "target": target,
            "message_id": bridge.last_sent_message_id(),
        }

    ok = bridge.send_message(msg, target_id=recipient_name, target_type="dm")
    if not ok:
        return {"ok": False, "error": "Failed to send window card", "recipient": recipient_name}
    return {
        "ok": True,
        "target_type": "dm",
        "target": recipient_name,
        "message_id": bridge.last_sent_message_id(),
    }
