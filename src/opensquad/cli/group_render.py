"""Shared group-message / approval / propose-options rendering for CLI."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

APPROVAL_RE = re.compile(
    r"\[\[(?:GROUP_APPROVAL|COLLAB_APPROVAL)\]\](.*?)\[\[/(?:GROUP_APPROVAL|COLLAB_APPROVAL)\]\]",
    re.DOTALL,
)
PROPOSE_RE = re.compile(r"\[\[PROPOSE_OPTIONS\]\](.*?)\[\[/PROPOSE_OPTIONS\]\]", re.DOTALL)


@dataclass
class PendingApproval:
    id: str
    title: str
    status: str
    group_id: str
    message_id: str = ""
    kind: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingProposal:
    id: str
    title: str
    options: list[tuple[str, str]]  # (label, value)
    group_id: str
    message_id: str = ""
    status: str = "pending"
    raw: dict[str, Any] = field(default_factory=dict)


def _message_sender_id(m: dict) -> str:
    sid = m.get("sender_id") or m.get("senderId") or m.get("user_id") or m.get("userId") or ""
    if not sid and isinstance(m.get("sender"), dict):
        sid = (m.get("sender") or {}).get("id") or (m.get("sender") or {}).get("user_id") or ""
    return str(sid or "").strip()


def sender_name(m: dict, *, names: dict[str, str] | None = None) -> str:
    """Best-effort display name for a group message sender."""
    sender = m.get("sender_name") or m.get("senderName")
    if not sender and isinstance(m.get("sender"), dict):
        sender = (m.get("sender") or {}).get("name")
    sid = _message_sender_id(m)
    if not sender and names and sid:
        sender = names.get(sid) or names.get(sid.lower())
    if not sender and sid:
        # Prefer id over "?" so history without sender_name is still readable
        sender = sid
    text = str(sender or "").strip()
    return text or "?"


def enrich_message_sender(m: dict, names: dict[str, str] | None = None) -> dict:
    """Copy message and fill sender_name from id→name map when missing."""
    if not isinstance(m, dict):
        return m
    out = dict(m)
    if out.get("sender_name") or out.get("senderName"):
        if not out.get("sender_name") and out.get("senderName"):
            out["sender_name"] = out.get("senderName")
        return out
    sid = _message_sender_id(out)
    if names and sid and (names.get(sid) or names.get(sid.lower())):
        out["sender_name"] = names.get(sid) or names.get(sid.lower())
    return out


def parse_approvals(content: str, *, group_id: str = "", message_id: str = "") -> list[PendingApproval]:
    out: list[PendingApproval] = []
    for m_obj in APPROVAL_RE.finditer(content or ""):
        try:
            payload = json.loads(m_obj.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        out.append(
            PendingApproval(
                id=str(payload.get("id") or ""),
                title=str(payload.get("title") or payload.get("step") or "approval"),
                status=str(payload.get("status") or "pending"),
                group_id=group_id or str(payload.get("group_id") or ""),
                message_id=message_id,
                kind=str(payload.get("kind") or ""),
                raw=payload,
            )
        )
    return out


def parse_proposals(content: str, *, group_id: str = "", message_id: str = "") -> list[PendingProposal]:
    out: list[PendingProposal] = []
    for m_obj in PROPOSE_RE.finditer(content or ""):
        try:
            payload = json.loads(m_obj.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        options: list[tuple[str, str]] = []
        for i, opt in enumerate(payload.get("options") or []):
            if isinstance(opt, dict):
                # Web / agent payload uses id+title; older CLI used label/value
                label = str(
                    opt.get("title")
                    or opt.get("label")
                    or opt.get("text")
                    or opt.get("name")
                    or opt.get("value")
                    or opt
                )
                value = str(opt.get("id") or opt.get("value") or label or f"opt_{i + 1}")
            else:
                label = value = str(opt)
            options.append((label, value))
        status = str(payload.get("status") or "pending").strip().lower() or "pending"
        out.append(
            PendingProposal(
                id=str(payload.get("id") or ""),
                title=str(payload.get("prompt") or payload.get("title") or "options"),
                options=options,
                group_id=group_id or str(payload.get("group_id") or ""),
                message_id=message_id,
                status=status,
                raw=payload,
            )
        )
    return out


def _proposal_chosen_labels(pr: PendingProposal) -> str:
    """Human summary of what was chosen / custom answer."""
    raw = pr.raw or {}
    custom = str(raw.get("custom_answer") or "").strip()
    if custom:
        return custom
    ids: list[str] = []
    for x in raw.get("chosen_option_ids") or []:
        s = str(x).strip()
        if s:
            ids.append(s)
    single = str(raw.get("chosen_option_id") or "").strip()
    if single and single not in ids:
        ids.insert(0, single)
    if not ids:
        return ""
    labels: list[str] = []
    by_id = {value: label for label, value in pr.options}
    for oid in ids:
        labels.append(by_id.get(oid, oid))
    return ", ".join(labels)


def format_message_lines(
    m: dict,
    *,
    shell_style: bool = True,
    show_raw_content: bool = True,
    member_names: dict[str, str] | None = None,
) -> list[str]:
    """
    Format a group chat message for terminal.

    Pending cards show numbered actions; already-resolved cards collapse to a hint.
    """
    if not isinstance(m, dict):
        return [str(m)]

    m = enrich_message_sender(m, member_names)
    lines: list[str] = []
    sender = sender_name(m, names=member_names)
    content = m.get("content") or ""
    mid = m.get("id") or ""
    gid = m.get("group_id") or ""

    # Strip card markers from visible body for cleaner display
    body = APPROVAL_RE.sub("", content)
    body = PROPOSE_RE.sub("", body).strip()
    # Propose-options encode also dumps a human list under the marker — drop that
    # fluff for history so resolved cards don't look like an active picker.
    has_propose = bool(PROPOSE_RE.search(content or ""))
    has_approval = bool(APPROVAL_RE.search(content or ""))
    if has_propose or has_approval:
        # Keep only a short lead-in before the numbered option dump
        lead: list[str] = []
        for ln in body.splitlines():
            s = ln.strip()
            if not s:
                continue
            if s[:1].isdigit() and (". " in s[:4] or "．" in s[:4]):
                break
            if s.startswith("请在下方") or s.startswith("Please choose") or s.startswith("请选择"):
                break
            lead.append(s)
            if len(lead) >= 2:
                break
        body = "\n".join(lead).strip()

    if show_raw_content:
        if body:
            lines.append(f"\n[{sender}] {body}")
        else:
            lines.append(f"\n[{sender}]")
    else:
        lines.append(f"\n[{sender}]")

    if mid and not shell_style:
        lines.append(f"  · id={mid}")

    for ap in parse_approvals(content, group_id=str(gid), message_id=str(mid)):
        lines.append(f"  ★ APPROVAL  {ap.id}  {ap.status}  — {ap.title}")
        if ap.status == "pending":
            lines.append("     [1] approve")
            lines.append("     [2] reject")
            if shell_style:
                lines.append(f"     → /approve {ap.id}   or   /reject {ap.id}")
                lines.append("     → or reply: 1 / 2")
            else:
                lines.append(f"     → opensquad group approve {gid or '<gid>'} {ap.id}")
        else:
            lines.append(f"     (already {ap.status})")

    for pr in parse_proposals(content, group_id=str(gid), message_id=str(mid)):
        st = (pr.status or "pending").lower()
        if st == "pending":
            lines.append(f"  ★ OPTIONS  {pr.id}  — {pr.title}")
            for i, (label, value) in enumerate(pr.options, 1):
                suffix = f"  (value={value})" if value != label else ""
                lines.append(f"     [{i}] {label}{suffix}")
            if shell_style:
                if pr.options:
                    lines.append(f"     → /choose {pr.id} <value>   or reply: 1..{len(pr.options)}")
            else:
                lines.append(f"     → opensquad group choose {gid or '<gid>'} {pr.id} <value>")
        else:
            # Resolved: collapse to a normal hint (do not re-offer interactive choose)
            chosen = _proposal_chosen_labels(pr)
            if st == "ignored":
                lines.append(f"  · OPTIONS dismissed — {pr.title}")
            elif chosen:
                lines.append(f"  · OPTIONS {st}: {chosen}")
            else:
                lines.append(f"  · OPTIONS {st} — {pr.title}")

    # Attachments as list (Web clickable chips → numbered list)
    attachments = m.get("attachments") or []
    if isinstance(attachments, list) and attachments:
        lines.append("  ★ FILES")
        for i, att in enumerate(attachments, 1):
            if isinstance(att, dict):
                name = att.get("name") or att.get("filename") or att.get("url") or "file"
                size = att.get("size") or ""
                extra = f"  ({size})" if size else ""
                lines.append(f"     [{i}] {name}{extra}")
            else:
                lines.append(f"     [{i}] {att}")

    return lines


def print_message(m: dict, *, shell_style: bool = True) -> None:
    for line in format_message_lines(m, shell_style=shell_style):
        print(line)


def print_option_menu(title: str, options: list[str]) -> None:
    """Generic Web-button → numbered list helper."""
    print(f"\n{title}")
    for i, opt in enumerate(options, 1):
        print(f"  [{i}] {opt}")
