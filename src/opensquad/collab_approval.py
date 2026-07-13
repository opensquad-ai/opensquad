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
    if not content:
        return None
    if "[[GROUP_APPROVAL]]" not in content and "[[COLLAB_APPROVAL]]" not in content:
        return None
    m = _MARKER_RE.search(content)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("id"):
        return None
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
) -> str:
    """Rewrite PROPOSE_OPTIONS marker JSON status inside an existing message body."""
    payload = parse_propose_options_payload(content)
    if not payload:
        return content
    payload["status"] = status
    if chosen:
        payload["chosen_option_id"] = chosen
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
    payload = parse_approval_payload(content)
    if not payload:
        return content
    payload["status"] = status
    if note:
        payload["resolve_note"] = note
    kind = normalize_kind(str(payload.get("kind") or KIND_GENERIC))
    if kind == KIND_COLLAB_STEP:
        start, end = COLLAB_APPROVAL_START, COLLAB_APPROVAL_END
    else:
        start, end = GROUP_APPROVAL_START, GROUP_APPROVAL_END
    new_marker = f"{start}{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}{end}"
    return _MARKER_RE.sub(new_marker, content, count=1)


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
