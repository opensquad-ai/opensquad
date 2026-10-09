"""
Group-chat approval helpers — encode/decode interactive Approve/Reject cards.

Message content format (TEXT), either marker is accepted:
  [[GROUP_APPROVAL]]{json}[[/GROUP_APPROVAL]]
  [[COLLAB_APPROVAL]]{json}[[/COLLAB_APPROVAL]]   (legacy alias)

Kinds:
  - collab_step  — collaboration 四门闸
  - mode_switch  — Plan ↔ Build
  - generic      — any other permission / confirmation request
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

GROUP_APPROVAL_START = "[[GROUP_APPROVAL]]"
GROUP_APPROVAL_END = "[[/GROUP_APPROVAL]]"
# Legacy collaboration marker (still parsed + encoded for collab_step default)
COLLAB_APPROVAL_START = "[[COLLAB_APPROVAL]]"
COLLAB_APPROVAL_END = "[[/COLLAB_APPROVAL]]"

# Propose-options marker (N-way single choice, distinct from approve/reject cards)
PROPOSE_OPTIONS_START = "[[PROPOSE_OPTIONS]]"
PROPOSE_OPTIONS_END = "[[/PROPOSE_OPTIONS]]"

# Back-compat aliases used by older imports
APPROVAL_START = COLLAB_APPROVAL_START
APPROVAL_END = COLLAB_APPROVAL_END

_MARKER_RE = re.compile(
    r"\[\[(?:GROUP_APPROVAL|COLLAB_APPROVAL)\]\]\s*(\{.*?\})\s*\[\[/(?:GROUP_APPROVAL|COLLAB_APPROVAL)\]\]",
    re.DOTALL,
)

# Tolerant marker reader. The regex above requires the closing tag, so a card whose end marker
# was lost — a truncated send, a hand-built message, an older client — parsed as *no card at
# all*: the chat painted the raw marker JSON in a normal bubble, and the resolve endpoint could
# not patch the status back into it (patch_approval_status_in_content found no match either).
_APPROVAL_START_RE = re.compile(r"\[\[(?:GROUP_APPROVAL|COLLAB_APPROVAL)\]\]")
_APPROVAL_END_RE = re.compile(r"\[\[/(?:GROUP_APPROVAL|COLLAB_APPROVAL)\]\]")


def _balanced_json_end(text: str, open_at: int) -> int:
    """Index just past the ``}`` closing the object at ``open_at`` (nested/in-string aware)."""
    depth = 0
    in_str = False
    esc = False
    for i in range(open_at, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def repair_json_text(text: str) -> str:
    """Escape raw control characters that sit inside JSON string literals.

    A card built by hand — the model typing the marker instead of calling the tool — can carry a
    real newline inside a value. That is not valid JSON at all: ``json.loads`` refuses it, the card
    parsed as *no card*, the chat painted the marker verbatim, and the resolve path could not
    rewrite the status either (it reads with the same reader). Repairing the reader's input keeps
    the display sane **and** lets the patch rewrite the marker properly, which heals the message.
    """
    out: list[str] = []
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            elif ord(ch) < 0x20:
                out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(ch, f"\\u{ord(ch):04x}"))
                continue
        elif ch == '"':
            in_str = True
        out.append(ch)
    return "".join(out)


def read_approval_marker(content: str) -> dict[str, Any] | None:
    """``{payload, start, end}`` for the marker in *content*, closing tag or not.

    ``start``/``end`` span the whole marker (opening tag through the closing one, or through the
    end of its JSON when the closing one is missing) so a caller can rewrite it in place.

    The JSON is located **first** and the closing tag looked for *after* it: a value may itself
    contain the tag's text (an agent quoting the marker inside its own summary), and searching for
    the tag first truncated the body mid-JSON and lost the card.
    """
    if not content:
        return None
    start_match = _APPROVAL_START_RE.search(content)
    if not start_match:
        return None
    open_at = content.find("{", start_match.end())
    if open_at < 0:
        return None
    close_at = _balanced_json_end(content, open_at)
    if close_at < 0:
        return None
    try:
        data = json.loads(repair_json_text(content[open_at:close_at]))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("id"):
        return None
    end_match = _APPROVAL_END_RE.search(content, close_at)
    return {
        "payload": data,
        "start": start_match.start(),
        "end": end_match.end() if end_match else close_at,
    }


_PROPOSE_OPTIONS_RE = re.compile(
    r"\[\[PROPOSE_OPTIONS\]\]\s*(\{.*?\})\s*\[\[/PROPOSE_OPTIONS\]\]",
    re.DOTALL,
)

KIND_COLLAB_STEP = "collab_step"
KIND_MODE_SWITCH = "mode_switch"
KIND_GENERIC = "generic"
VALID_KINDS = frozenset({KIND_COLLAB_STEP, KIND_MODE_SWITCH, KIND_GENERIC})

STEP_ALIASES = {
    "requirements": "确定需求",
    "requirement": "确定需求",
    "确定需求": "确定需求",
    "plan": "讨论方案",
    "方案": "讨论方案",
    "讨论方案": "讨论方案",
    "task_assign": "任务分配",
    "assign": "任务分配",
    "任务分配": "任务分配",
    "acceptance": "任务验收",
    "验收": "任务验收",
    "任务验收": "任务验收",
}


def normalize_step(step: str) -> str:
    s = (step or "").strip()
    if not s:
        return "下一步"
    return STEP_ALIASES.get(s, STEP_ALIASES.get(s.lower(), s))


def normalize_kind(kind: str) -> str:
    k = (kind or KIND_GENERIC).strip().lower()
    if k in ("collab", "collaboration", "step", "gate"):
        return KIND_COLLAB_STEP
    if k in ("mode", "agent_mode", "plan_build", "switch"):
        return KIND_MODE_SWITCH
    if k in VALID_KINDS:
        return k
    return KIND_GENERIC


def new_approval_id() -> str:
    return f"appr_{uuid.uuid4().hex[:12]}"


def build_approval_payload(
    *,
    approval_id: str,
    title: str,
    summary: str = "",
    agent_id: str = "",
    agent_name: str = "",
    group_id: str = "",
    status: str = "pending",
    kind: str = KIND_GENERIC,
    # collab_step
    collab_id: str = "",
    step: str = "",
    pm_agent_id: str = "",
    pm_agent_name: str = "",
    # mode_switch
    from_mode: str = "",
    to_mode: str = "",
    # opaque extra for generic
    action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind_n = normalize_kind(kind)
    aid = (agent_id or pm_agent_id or "").strip()
    aname = (agent_name or pm_agent_name or aid).strip()
    payload: dict[str, Any] = {
        "v": 1,
        "id": approval_id,
        "kind": kind_n,
        "title": (title or "").strip() or "批准请求",
        "summary": (summary or "").strip(),
        "status": status,
        "agent_id": aid,
        "agent_name": aname,
        "group_id": group_id,
        # legacy fields so older resolve paths keep working
        "pm_agent_id": aid,
        "pm_agent_name": aname,
    }
    if kind_n == KIND_COLLAB_STEP:
        step_label = normalize_step(step)
        payload["collab_id"] = collab_id
        payload["step"] = step_label
        if not payload["title"] or payload["title"] == "批准请求":
            payload["title"] = step_label
    elif kind_n == KIND_MODE_SWITCH:
        payload["from_mode"] = (from_mode or "").strip().lower()
        payload["to_mode"] = (to_mode or "").strip().lower()
        if not payload["title"] or payload["title"] == "批准请求":
            fm = payload["from_mode"] or "?"
            tm = payload["to_mode"] or "?"
            payload["title"] = f"切换模式：{fm} → {tm}"
    if action:
        payload["action"] = action
    return payload


def encode_approval_message(payload: dict[str, Any]) -> str:
    """Build group TEXT content with machine marker + readable fallback."""
    kind = normalize_kind(str(payload.get("kind") or KIND_GENERIC))
    # Prefer GROUP_APPROVAL for non-collab; keep COLLAB marker for collab_step compat
    if kind == KIND_COLLAB_STEP:
        start, end = COLLAB_APPROVAL_START, COLLAB_APPROVAL_END
        headline = "📋 协作批准请求"
    else:
        start, end = GROUP_APPROVAL_START, GROUP_APPROVAL_END
        if kind == KIND_MODE_SWITCH:
            headline = "🔄 模式切换申请"
        else:
            headline = "✋ 批准请求"

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    title = payload.get("title") or "批准请求"
    summary = payload.get("summary") or ""
    lines = [f"{start}{body}{end}", f"{headline}：{title}"]
    if kind == KIND_COLLAB_STEP and payload.get("step"):
        lines.append(f"环节：{payload.get('step')}")
    if kind == KIND_MODE_SWITCH:
        fm = payload.get("from_mode") or "?"
        tm = payload.get("to_mode") or "?"
        lines.append(f"模式：{fm} → {tm}")
    if summary:
        lines.append(str(summary))
    lines.append("请在下方卡片中点击「确定」或「拒绝」。")
    return "\n".join(lines)


def parse_approval_payload(content: str) -> dict[str, Any] | None:
    found = read_approval_marker(content or "")
    if not found:
        return None
    data: dict[str, Any] = found["payload"]
    # Normalize kind for legacy collab cards that omit it
    if not data.get("kind"):
        if data.get("collab_id") or data.get("step"):
            data["kind"] = KIND_COLLAB_STEP
        elif data.get("to_mode"):
            data["kind"] = KIND_MODE_SWITCH
        else:
            data["kind"] = KIND_GENERIC
    if not data.get("agent_id") and data.get("pm_agent_id"):
        data["agent_id"] = data["pm_agent_id"]
    if not data.get("agent_name") and data.get("pm_agent_name"):
        data["agent_name"] = data["pm_agent_name"]
    return data


def encode_propose_options_message(payload: dict[str, Any]) -> str:
    """Build group TEXT content for an N-way propose-options card."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    prompt = payload.get("prompt") or "请选择一个选项"
    options = payload.get("options") or []
    lines = [f"{PROPOSE_OPTIONS_START}{body}{PROPOSE_OPTIONS_END}", f"❓ 选择一个选项：{prompt}"]
    for i, opt in enumerate(options):
        if isinstance(opt, dict):
            title = opt.get("title") or ""
            desc = opt.get("description") or ""
            line = f"{i + 1}. {title}"
            if desc:
                line += f" — {desc}"
            lines.append(line)
    lines.append("请在下方卡片中选择一个选项。")
    return "\n".join(lines)


def parse_propose_options_payload(content: str) -> dict[str, Any] | None:
    if not content or PROPOSE_OPTIONS_START not in content:
        return None
    m = _PROPOSE_OPTIONS_RE.search(content)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("id"):
        return None
    if not data.get("options") or not isinstance(data["options"], list):
        return None
    return data


def patch_propose_options_status_in_content(
    content: str,
    status: str,
    chosen: str = "",
    custom: str = "",
    note: str = "",
    chosen_ids: list[str] | None = None,
) -> str:
    """Rewrite PROPOSE_OPTIONS marker JSON status inside an existing message body."""
    payload = parse_propose_options_payload(content)
    if not payload:
        return content
    payload["status"] = status
    ids = [str(x).strip() for x in (chosen_ids or []) if str(x).strip()]
    if not ids and chosen:
        ids = [c.strip() for c in str(chosen).split(",") if c.strip()]
    if ids:
        payload["chosen_option_ids"] = ids
        payload["chosen_option_id"] = ids[0]
    if custom:
        payload["custom_answer"] = custom
    if note:
        payload["resolve_note"] = note
    new_marker = (
        f"{PROPOSE_OPTIONS_START}{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}{PROPOSE_OPTIONS_END}"
    )
    return _PROPOSE_OPTIONS_RE.sub(new_marker, content, count=1)


def post_group_propose_options_card(payload: dict[str, Any], group_id: str) -> dict[str, Any]:
    """Send an N-way propose-options card to a group via bridge. Returns status dict."""
    from opensquad.bridge import bridge

    if not bridge or not bridge.token:
        return {"ok": False, "error": "Bridge not connected"}

    target = group_id
    groups = bridge.list_groups_api() or []
    if not any(isinstance(g, dict) and g.get("id") == group_id for g in groups):
        for g in groups:
            if isinstance(g, dict) and g.get("name") == group_id:
                target = str(g.get("id") or group_id)
                break

    msg = encode_propose_options_message(payload)
    ok = bridge.send_message(msg, target_id=target, target_type="group")
    if not ok:
        return {"ok": False, "error": "Failed to send propose-options card", "group_id": target}

    message_id = bridge.last_sent_message_id()
    return {"ok": True, "group_id": target, "message_id": message_id}


def patch_approval_status_in_content(content: str, status: str, note: str = "") -> str:
    """Rewrite marker JSON status inside an existing message body."""
    found = read_approval_marker(content or "")
    if not found:
        return content
    payload: dict[str, Any] = found["payload"]
    payload["status"] = status
    if note:
        payload["resolve_note"] = note
    kind = normalize_kind(str(payload.get("kind") or KIND_GENERIC))
    if kind == KIND_COLLAB_STEP:
        start, end = COLLAB_APPROVAL_START, COLLAB_APPROVAL_END
    else:
        start, end = GROUP_APPROVAL_START, GROUP_APPROVAL_END
    new_marker = f"{start}{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}{end}"
    # Span the whole marker, so a body that lost its closing tag comes back well-formed — that is
    # what lets the clients see the decision at all (they filter a decided card out of the chat).
    return content[: found["start"]] + new_marker + content[found["end"] :]


def resolve_current_group_id(explicit: str = "") -> str:
    """Best-effort group id: explicit arg → runner turn context → parse last input."""
    if (explicit or "").strip():
        return explicit.strip()
    try:
        import opensquad.runner as runner_mod

        r = getattr(runner_mod, "_active_runner", None)
        if r is not None:
            gid = str(getattr(r, "_current_group_id", "") or "").strip()
            if gid:
                return gid
            # Fallback: scrape from last user input formatting
            last = str(getattr(r, "_last_user_input", "") or "")
            m = re.search(r"group_id=([A-Za-z0-9_\-]+)", last)
            if m:
                return m.group(1)
            channel = str(getattr(r, "_current_channel", "") or "")
            if channel == "chatpro_group":
                # source_chat_id sometimes holds group id for chatpro
                sid = str(getattr(r, "_current_source_chat_id", "") or "").strip()
                if sid:
                    return sid
    except Exception:
        pass
    return ""


def resolve_agent_identity() -> tuple[str, str]:
    """Return (agent_id, agent_name) for the running agent."""
    try:
        import os

        from opensquad.input_hub import input_hub

        agent_dir = input_hub.agent_dir or ""
        folder = os.path.basename(agent_dir) if agent_dir else ""
        agent_id = folder
        agent_name = folder
        try:
            from opensquad.json_cache import load_json_cached

            cfg = load_json_cached(os.path.join(agent_dir, "config.json")) if agent_dir else None
            if isinstance(cfg, dict):
                agent_id = str(cfg.get("agent_id") or folder)
                agent_name = str(cfg.get("agent_name") or folder)
        except Exception:
            pass
        return agent_id, agent_name
    except Exception:
        return "", ""


def post_group_approval_card(payload: dict[str, Any], group_id: str) -> dict[str, Any]:
    """Send encoded approval message to a group via bridge. Returns status dict."""
    from opensquad.bridge import bridge

    if not bridge or not bridge.token:
        return {"ok": False, "error": "Bridge not connected"}

    target = group_id
    groups = bridge.list_groups_api() or []
    if not any(isinstance(g, dict) and g.get("id") == group_id for g in groups):
        for g in groups:
            if isinstance(g, dict) and g.get("name") == group_id:
                target = str(g.get("id") or group_id)
                break

    msg = encode_approval_message(payload)
    ok = bridge.send_message(msg, target_id=target, target_type="group")
    if not ok:
        return {"ok": False, "error": "Failed to send approval card", "group_id": target}

    message_id = bridge.last_sent_message_id()
    if not message_id:
        try:
            hist = bridge.get_group_history(target, limit=8) or []
            aid = str(payload.get("id") or "")
            for m in hist:
                if isinstance(m, dict) and aid and aid in str(m.get("content") or ""):
                    message_id = str(m.get("id") or "")
                    break
        except Exception:
            pass
    return {"ok": True, "group_id": target, "message_id": message_id}


# ---------------------------------------------------------------------------
# Collaboration task cards — [[COLLAB_TASK]]{json}[[/COLLAB_TASK]]
#
# These announce a collaboration task (invite / assignment / progress /
# discussion / done) as a clickable card in group chat and in DMs. Like the
# approval cards above they ride on TEXT content (no schema change); the UI
# parses the marker out and opens the task window keyed by ``collab_id``.
# ---------------------------------------------------------------------------
COLLAB_TASK_START = "[[COLLAB_TASK]]"
COLLAB_TASK_END = "[[/COLLAB_TASK]]"

_COLLAB_TASK_RE = re.compile(
    r"\[\[COLLAB_TASK\]\]\s*(\{.*?\})\s*\[\[/COLLAB_TASK\]\]",
    re.DOTALL,
)

TASK_KIND_INVITE = "invite"
TASK_KIND_ASSIGN = "assign"
TASK_KIND_PROGRESS = "progress"
TASK_KIND_DISCUSSION = "discussion"
TASK_KIND_DONE = "done"
VALID_TASK_KINDS = frozenset(
    {TASK_KIND_INVITE, TASK_KIND_ASSIGN, TASK_KIND_PROGRESS, TASK_KIND_DISCUSSION, TASK_KIND_DONE}
)

PARTICIPANT_INVITED = "invited"
PARTICIPANT_ACCEPTED = "accepted"
PARTICIPANT_DECLINED = "declined"
VALID_PARTICIPANT_STATES = frozenset({PARTICIPANT_INVITED, PARTICIPANT_ACCEPTED, PARTICIPANT_DECLINED})

_TASK_HEADLINES = {
    TASK_KIND_INVITE: "🤝 协作邀请",
    TASK_KIND_ASSIGN: "📌 任务分配",
    TASK_KIND_PROGRESS: "📈 任务进度",
    TASK_KIND_DISCUSSION: "💬 任务讨论",
    TASK_KIND_DONE: "✅ 协作完成",
}


def new_task_card_id() -> str:
    return f"ctask_{uuid.uuid4().hex[:12]}"


def normalize_task_kind(kind: str) -> str:
    k = (kind or "").strip().lower()
    if k in VALID_TASK_KINDS:
        return k
    if k in ("start", "open", "join", "collab_start"):
        return TASK_KIND_INVITE
    if k in ("task", "assign_task", "assignment"):
        return TASK_KIND_ASSIGN
    if k in ("update", "status"):
        return TASK_KIND_PROGRESS
    if k in ("chat", "message", "talk"):
        return TASK_KIND_DISCUSSION
    if k in ("finish", "finished", "end", "close"):
        return TASK_KIND_DONE
    return TASK_KIND_DISCUSSION


def normalize_participant_state(state: str) -> str:
    s = (state or "").strip().lower()
    if s in VALID_PARTICIPANT_STATES:
        return s
    if s in ("received", "pending", "sent", "ack"):
        return PARTICIPANT_INVITED
    if s in ("joined", "accept", "ok", "in"):
        return PARTICIPANT_ACCEPTED
    if s in ("reject", "refused", "no", "out"):
        return PARTICIPANT_DECLINED
    return PARTICIPANT_INVITED


def build_collab_task_payload(
    *,
    collab_id: str,
    title: str,
    kind: str = TASK_KIND_INVITE,
    group_id: str = "",
    participants: list[dict[str, Any]] | None = None,
    status: str = "open",
    summary: str = "",
    card: str = "",
    card_id: str = "",
    agent_id: str = "",
    agent_name: str = "",
    task_card_id: str = "",
) -> dict[str, Any]:
    """Payload for a collaboration-task card. ``participants`` entries are
    ``{agent_id, name, state}`` with state in invited/accepted/declined."""
    parts: list[dict[str, str]] = []
    for p in participants or []:
        if not isinstance(p, dict):
            continue
        aid = str(p.get("agent_id") or p.get("id") or "").strip()
        if not aid:
            continue
        parts.append(
            {
                "agent_id": aid,
                "name": str(p.get("name") or aid).strip(),
                "state": normalize_participant_state(str(p.get("state") or "")),
            }
        )
    return {
        "v": 1,
        "id": task_card_id or new_task_card_id(),
        "kind": normalize_task_kind(kind),
        "collab_id": (collab_id or "").strip(),
        "title": (title or "").strip() or "协作任务",
        "summary": (summary or "").strip(),
        "card": (card or "").strip(),
        "card_id": (card_id or "").strip(),
        "group_id": group_id,
        "status": status,
        "participants": parts,
        "agent_id": agent_id,
        "agent_name": agent_name or agent_id,
    }


def encode_collab_task_marker(payload: dict[str, Any]) -> str:
    """The machine marker alone — the one place the wire format is written.

    Callers that carry their own readable body (the group invitation does) use this
    instead of re-serialising the payload by hand, so marker and payload cannot drift.
    """
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{COLLAB_TASK_START}{body}{COLLAB_TASK_END}"


def encode_collab_task_message(payload: dict[str, Any]) -> str:
    """Build chat TEXT content: machine marker first, then a readable fallback
    (agents read this text too, so the task id / card / join hint stay)."""
    kind = normalize_task_kind(str(payload.get("kind") or ""))
    headline = _TASK_HEADLINES.get(kind, "🤝 协作任务")
    cid = str(payload.get("collab_id") or "")
    lines = [
        encode_collab_task_marker(payload),
        f"{headline}：{payload.get('title') or '协作任务'}",
        f"Task ID: {cid}",
    ]
    card = str(payload.get("card") or "")
    if card:
        lines.append(f"Collab Card: {card}")
    parts = payload.get("participants") or []
    if isinstance(parts, list) and parts:
        labels = {PARTICIPANT_INVITED: "已邀请", PARTICIPANT_ACCEPTED: "已参与", PARTICIPANT_DECLINED: "已拒绝"}
        rendered = [
            f"{p.get('name') or p.get('agent_id')}({labels.get(str(p.get('state')), str(p.get('state')))})"
            for p in parts
            if isinstance(p, dict)
        ]
        if rendered:
            lines.append("参与人员: " + "、".join(rendered))
    summary = str(payload.get("summary") or "")
    if summary:
        lines.append(summary)
    if kind == TASK_KIND_INVITE:
        lines.append(
            f'You\'re invited to join -- consider calling: join_collaboration(card="{card}", collab_id="{cid}")'
        )
    lines.append(f'All board updates/reads must include collab_id="{cid}"')
    return "\n".join(lines)


def parse_collab_task_payload(content: str) -> dict[str, Any] | None:
    if not content or COLLAB_TASK_START not in content:
        return None
    m = _COLLAB_TASK_RE.search(content)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("id") or not data.get("collab_id"):
        return None
    data["kind"] = normalize_task_kind(str(data.get("kind") or ""))
    parts = data.get("participants")
    data["participants"] = (
        [
            {
                "agent_id": str(p.get("agent_id") or ""),
                "name": str(p.get("name") or p.get("agent_id") or ""),
                "state": normalize_participant_state(str(p.get("state") or "")),
            }
            for p in parts
            if isinstance(p, dict) and (p.get("agent_id") or p.get("id"))
        ]
        if isinstance(parts, list)
        else []
    )
    return data


def strip_collab_task_marker(content: str) -> str:
    """Drop only this module's own marker line, keeping the readable text."""
    if not content or COLLAB_TASK_START not in content:
        return content
    return _COLLAB_TASK_RE.sub("", content).strip()


def _rewrite_collab_task_marker(content: str, payload: dict[str, Any]) -> str:
    return _COLLAB_TASK_RE.sub(encode_collab_task_marker(payload), content, count=1)


def patch_collab_task_participant_in_content(content: str, agent_id: str, state: str) -> str:
    """Set one participant's state (invited → accepted / declined) in the marker."""
    payload = parse_collab_task_payload(content)
    if not payload or not agent_id:
        return content
    target = str(agent_id)
    changed = False
    for p in payload.get("participants") or []:
        if isinstance(p, dict) and str(p.get("agent_id")) == target:
            p["state"] = normalize_participant_state(state)
            changed = True
    if not changed:
        payload.setdefault("participants", []).append(
            {
                "agent_id": target,
                "name": target,
                "state": normalize_participant_state(state),
            }
        )
    return _rewrite_collab_task_marker(content, payload)
