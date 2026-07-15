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
    raw: dict[str, Any] = field(default_factory=dict)


def sender_name(m: dict) -> str:
    sender = m.get("sender_name")
    if not sender and isinstance(m.get("sender"), dict):
        sender = (m.get("sender") or {}).get("name")
    return str(sender or m.get("user_id") or "?")


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
        out.append(
            PendingProposal(
                id=str(payload.get("id") or ""),
                title=str(payload.get("prompt") or payload.get("title") or "options"),
                options=options,
                group_id=group_id or str(payload.get("group_id") or ""),
                message_id=message_id,
                raw=payload,
            )
        )
    return out


def format_message_lines(
    m: dict,
    *,
    shell_style: bool = True,
    show_raw_content: bool = True,
) -> list[str]:
    """
    Format a group chat message for terminal.

    Web clickable cards become numbered list options:
      ★ APPROVAL …
         [1] approve    [2] reject
      ★ OPTIONS …
         [1] FastAPI    [2] Flask
    """
    if not isinstance(m, dict):
        return [str(m)]

    lines: list[str] = []
    sender = sender_name(m)
    content = m.get("content") or ""
    mid = m.get("id") or ""
    gid = m.get("group_id") or ""

    # Strip card markers from visible body for cleaner display
    body = APPROVAL_RE.sub("", content)
    body = PROPOSE_RE.sub("", body).strip()

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
        lines.append(f"  ★ OPTIONS  {pr.id}  — {pr.title}")
        for i, (label, value) in enumerate(pr.options, 1):
            suffix = f"  (value={value})" if value != label else ""
            lines.append(f"     [{i}] {label}{suffix}")
        if shell_style:
            if pr.options:
                lines.append(f"     → /choose {pr.id} <value>   or reply: 1..{len(pr.options)}")
        else:
            lines.append(f"     → opensquad group choose {gid or '<gid>'} {pr.id} <value>")

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
