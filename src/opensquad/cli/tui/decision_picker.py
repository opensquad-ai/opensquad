"""OpenCode-style decision cards for TUI (propose_options / mode_switch / group)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class DecisionOption:
    id: str
    title: str
    description: str = ""


@dataclass
class PendingDecision:
    """One interactive decision waiting for ↑↓ / Enter / Esc."""

    kind: str  # options | mode_switch | group_options | group_approval
    id: str
    prompt: str
    options: list[DecisionOption]
    allow_custom: bool = False
    allow_multiple: bool = False
    index: int = 0
    selected_ids: set[str] = field(default_factory=set)
    from_mode: str = ""
    to_mode: str = ""
    group_id: str = ""
    message_id: str = ""
    source: str = "solo"  # solo | group
    # Synthetic row id for "Type your own answer"
    custom_row_id: str = "__custom__"

    @property
    def rows(self) -> list[DecisionOption]:
        rows = list(self.options)
        if self.allow_custom and self.kind in ("options", "group_options"):
            rows.append(
                DecisionOption(
                    id=self.custom_row_id,
                    title="Type your own answer",
                    description="输入自定义回复",
                )
            )
        return rows

    def clamp_index(self) -> None:
        rows = self.rows
        if not rows:
            self.index = 0
            return
        self.index = max(0, min(self.index, len(rows) - 1))


def normalize_options(raw: Any) -> list[DecisionOption]:
    out: list[DecisionOption] = []
    if not isinstance(raw, list):
        return out
    for i, opt in enumerate(raw):
        if isinstance(opt, dict):
            oid = str(opt.get("id") or opt.get("value") or f"opt_{i + 1}").strip()
            title = str(opt.get("title") or opt.get("label") or opt.get("text") or opt.get("name") or oid).strip()
            desc = str(opt.get("description") or opt.get("summary") or "").strip()
        else:
            oid = f"opt_{i + 1}"
            title = str(opt).strip()
            desc = ""
        if title:
            out.append(DecisionOption(id=oid or f"opt_{i + 1}", title=title, description=desc))
    return out


def from_propose_options(data: dict[str, Any], *, source: str = "solo") -> PendingDecision | None:
    rid = str(data.get("id") or "").strip()
    options = normalize_options(data.get("options"))
    if not rid or len(options) < 1:
        return None
    return PendingDecision(
        kind="options" if source == "solo" else "group_options",
        id=rid,
        prompt=str(data.get("prompt") or data.get("title") or "请选择："),
        options=options,
        allow_custom=data.get("allow_custom") is not False,
        allow_multiple=bool(data.get("allow_multiple")),
        group_id=str(data.get("group_id") or ""),
        message_id=str(data.get("message_id") or ""),
        source=source,
        index=0,
    )


def from_mode_switch(data: dict[str, Any]) -> PendingDecision | None:
    rid = str(data.get("id") or "").strip()
    to_mode = str(data.get("to_mode") or "").strip().lower()
    from_mode = str(data.get("from_mode") or "").strip().lower()
    if not rid or to_mode not in ("plan", "build"):
        return None
    reason = str(data.get("reason") or data.get("text") or "").strip()
    prompt = f"切换模式 {from_mode or '?'} → {to_mode}"
    if reason:
        prompt = f"{prompt}\n{reason}"
    return PendingDecision(
        kind="mode_switch",
        id=rid,
        prompt=prompt,
        options=[
            DecisionOption(id="approve", title="Approve", description=f"切换到 {to_mode}"),
            DecisionOption(id="deny", title="Deny", description="拒绝此次模式切换"),
        ],
        allow_custom=False,
        allow_multiple=False,
        from_mode=from_mode,
        to_mode=to_mode,
        source="solo",
        index=0,
    )


def from_group_approval(
    *,
    approval_id: str,
    title: str,
    group_id: str = "",
    message_id: str = "",
    summary: str = "",
) -> PendingDecision:
    return PendingDecision(
        kind="group_approval",
        id=approval_id,
        prompt=title or "Approval required",
        options=[
            DecisionOption(id="approve", title="Approve", description=summary or "批准"),
            DecisionOption(id="deny", title="Reject", description="拒绝"),
        ],
        allow_custom=False,
        group_id=group_id,
        message_id=message_id,
        source="group",
        index=0,
    )


def render_decision_markup(
    decision: PendingDecision,
    *,
    escape: Callable[[str], str],
    primary: str = "#58a6ff",
    muted: str = "#8b949e",
    fg: str = "#e6edf3",
) -> str:
    """OpenCode-like numbered list with highlighted selection."""
    decision.clamp_index()
    rows = decision.rows
    lines: list[str] = []
    for i, para in enumerate(str(decision.prompt or "").splitlines() or ["请选择："]):
        style = "bold" if i == 0 else "dim"
        lines.append(f"[{style} {fg}]{escape(para)}[/]")
    lines.append("")
    for i, opt in enumerate(rows):
        n = i + 1
        multi_mark = ""
        if decision.allow_multiple and opt.id != decision.custom_row_id:
            multi_mark = "✓ " if opt.id in decision.selected_ids else "  "
        title = escape(opt.title)
        if i == decision.index:
            lines.append(f"[bold {primary}]{multi_mark}{n}. {title}[/]")
        else:
            lines.append(f"[{fg}]{multi_mark}{n}. {title}[/]")
        if opt.description:
            desc = escape(opt.description)
            pad = "   " if not multi_mark else "     "
            lines.append(f"[dim {muted}]{pad}{desc}[/]")
    hint = "↑↓ select · enter submit · esc dismiss"
    if decision.allow_multiple:
        hint = "↑↓ · space toggle · enter submit · esc dismiss"
    if decision.allow_custom:
        hint += " · 可选自定义"
    lines.append("")
    lines.append(f"[dim {muted}]{hint}[/]")
    return "\n".join(lines)
