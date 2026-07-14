"""Claude-Code-style welcome banner for the interactive shell."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from opensquad import __version__

_CYAN = "\033[36m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"
_YELLOW = "\033[33m"


def _width() -> int:
    try:
        return max(60, min(shutil.get_terminal_size((80, 24)).columns, 100))
    except Exception:
        return 80


def _box(lines: list[str], width: int) -> str:
    inner = width - 2
    top = "╭" + "─" * inner + "╮"
    bot = "╰" + "─" * inner + "╯"
    out = [top]
    for line in lines:
        visible = _visible_len(line)
        pad = max(0, inner - visible - 1)
        out.append("│ " + line + (" " * pad) + "│")
    out.append(bot)
    return "\n".join(out)


def _visible_len(s: str) -> int:
    import re

    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def render_banner(
    *,
    agent: str | None,
    gateway_url: str,
    email: str | None,
    cwd: str | None = None,
) -> str:
    w = _width()
    cwd = cwd or os.getcwd()
    home = str(Path.home())
    cwd_disp = ("~" + cwd[len(home) :]) if cwd.startswith(home) else cwd

    left = [
        f"{_BOLD}OpenSquad CLI{_RESET} {_DIM}v{__version__}{_RESET}",
        "",
        "  ╱╲╱╲  multi-agent · group chat",
        "  ╲╱╲╱  Web parity · text UI",
        "",
        f"{_YELLOW}Welcome back!{_RESET}" if email else f"{_YELLOW}Welcome!{_RESET}",
        f"{_DIM}Agent:{_RESET} {agent or '(none)'}  ·  {_DIM}Gateway:{_RESET} {gateway_url}",
        f"{_DIM}{cwd_disp}{_RESET}",
    ]

    tips = [
        f"{_BOLD}Tips for getting started{_RESET}",
        "  · Same features as Web — slash commands, not buttons",
        f"  · {_CYAN}/help{_RESET}  {_CYAN}/skill list{_RESET}  {_CYAN}/model list{_RESET}",
        f"  · {_CYAN}/group join g-default{_RESET} for squad chat",
        f"  · Cards/options → numbered {_CYAN}[1] [2]{_RESET} (type number to pick)",
        f"  · Ctrl+V / {_CYAN}/image{_RESET} attach image (sent, not previewed)",
        f"  · Bottom box: {_CYAN}Enter{_RESET} send · {_CYAN}Alt+Enter{_RESET} newline",
        f"  · {_CYAN}/sk{_RESET}+Tab fuzzy-completes /skill…",
        "",
        f"{_BOLD}Modes{_RESET}",
        f"  {_DIM}solo{_RESET} = agent DM   {_DIM}group{_RESET} = multi-agent room  {_CYAN}/leave{_RESET}",
    ]

    if w < 90:
        return f"{_CYAN}{_box(left + [''] + tips, w)}{_RESET}"

    return (
        f"{_CYAN}{_box(left, w)}{_RESET}\n"
        f"{_CYAN}{_box(tips, w)}{_RESET}\n"
        f"{_DIM}────────────────────────────────────────{_RESET}\n"
    )


def status_right(
    agent: str | None = None,
    *,
    mode: str = "solo",
    group_name: str | None = None,
    pending_n: int = 0,
) -> str:
    bits = []
    if mode == "group":
        bits.append(group_name or "group")
        bits.append("group")
        bits.append("/leave")
    else:
        bits.append(agent or "—")
        bits.append("solo")
        bits.append("/help")
    if pending_n:
        bits.append(f"+{pending_n}img")
    return " · ".join(bits)
