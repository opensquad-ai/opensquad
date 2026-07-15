"""Parse <plan> / task-list content and render OpenCode-style Todos for the TUI."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

PlanStatus = Literal["pending", "running", "done", "failed"]


@dataclass
class PlanStep:
    content: str
    status: PlanStatus = "pending"


_STATUS_DONE = re.compile(r"\[(?:x|X|✔|✓|done|completed)\]", re.I)
_STATUS_RUN = re.compile(r"\[(?:>|running|in[_\s-]?progress|current)\]", re.I)
_STATUS_FAIL = re.compile(r"\[(?:failed|error)\]", re.I)
_STATUS_ANY = re.compile(
    r"\[(?:x|X|✔|✓|done|completed|>|running|in[_\s-]?progress|current|failed|error|\s*)\]",
    re.I,
)
_LEAD = re.compile(r"^[-*\d.)\s]+")
_TAG_THINK = re.compile(r"</?(?:think|thought)\b[^>]*>", re.I)
_TAG_PLAN = re.compile(r"</?plan\b[^>]*>", re.I)


def isolate_plan_body(raw: str) -> str:
    text = _TAG_THINK.sub("\n", str(raw or ""))
    lower = text.lower()
    open_idx = lower.rfind("<plan")
    if open_idx >= 0:
        after = text[open_idx:]
        m = re.search(r"<plan\b[^>]*>([\s\S]*?)(?:</plan\s*>|$)", after, re.I)
        if m:
            return m.group(1)
    return _TAG_PLAN.sub("", text)


def _clean_step(value: str) -> str:
    s = _TAG_THINK.sub("", str(value or ""))
    s = _TAG_PLAN.sub("", s)
    s = s.strip().strip("`").strip()
    return s


def _status_from_line(line: str) -> PlanStatus:
    if _STATUS_DONE.search(line):
        return "done"
    if _STATUS_RUN.search(line):
        return "running"
    if _STATUS_FAIL.search(line):
        return "failed"
    return "pending"


def parse_plan_content(content: Any) -> list[PlanStep]:
    """Align with Web PlanBlock.parsePlanContent."""
    if not content:
        return []

    if isinstance(content, list):
        out: list[PlanStep] = []
        for item in content:
            if isinstance(item, str):
                text = _clean_step(item)
                if text:
                    out.append(PlanStep(content=text, status="pending"))
                continue
            if isinstance(item, dict):
                text = _clean_step(str(item.get("content") or item.get("text") or item.get("step") or item))
                st = str(item.get("status") or "pending").lower()
                status: PlanStatus = st if st in ("pending", "running", "done", "failed") else "pending"
                if text:
                    out.append(PlanStep(content=text, status=status))
        return out

    text = content if isinstance(content, str) else str(content)
    text = isolate_plan_body(text)
    steps: list[PlanStep] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        status = _status_from_line(line)
        trimmed = _LEAD.sub("", line).strip()
        cleaned = _STATUS_ANY.sub("", trimmed).strip()
        cleaned = _clean_step(cleaned)
        if cleaned:
            steps.append(PlanStep(content=cleaned, status=status))
    return steps


def format_opencode_todos_markup(
    steps: list[PlanStep],
    *,
    title: str = "Todos",
    fg: str = "#c9d1d9",
    muted: str = "#8b949e",
    green: str = "#3fb950",
    cyan: str = "#58a6ff",
    red: str = "#f85149",
) -> list[str]:
    """OpenCode-like:

    # Todos
    [✔] done step
    [>] running step
    [ ] pending step
    """
    if not steps:
        return []
    lines: list[str] = [f"[bold {fg}]# {title}[/]"]
    for step in steps:
        text = step.content.replace("[", "\\[")
        if step.status == "done":
            lines.append(f"[{green}]\\[✔][/] [{muted}]{text}[/]")
        elif step.status == "running":
            lines.append(f"[{cyan}]\\[>][/] [bold {fg}]{text}[/]")
        elif step.status == "failed":
            lines.append(f"[{red}]\\[✗][/] [{red}]{text}[/]")
        else:
            lines.append(f"[{muted}]\\[ ][/] [{fg}]{text}[/]")
    done = sum(1 for s in steps if s.status == "done")
    total = len(steps)
    if total:
        lines.append(f"[dim {muted}]{done}/{total} done[/]")
    return lines


def markup_from_plan_payload(payload: Any) -> list[str] | None:
    """Build Todos markup from a WS plan event payload ({id, text} or raw string)."""
    if payload is None:
        return None
    if isinstance(payload, str):
        content = payload
    elif isinstance(payload, dict):
        content = payload.get("text") or payload.get("content") or payload.get("plan")
        if content is None and isinstance(payload.get("steps"), list):
            content = payload["steps"]
    else:
        content = str(payload)
    steps = parse_plan_content(content)
    if not steps:
        return None
    return format_opencode_todos_markup(steps)
