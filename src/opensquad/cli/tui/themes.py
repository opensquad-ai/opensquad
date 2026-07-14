"""OpenSquad TUI themes — Textual Theme registry + persistence.

Builtin Textual themes (nord, monokai, …) are reused; we add OpenCode-style
extras (opencode, matrix, mercury, …) and remember the last choice.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from textual.theme import Theme

THEME_PREF_PATH = Path.home() / ".opensquad" / "cli_theme.json"
DEFAULT_THEME = "opencode"

# Extra themes (OpenCode-inspired names). Built-ins are already registered by Textual.
_CUSTOM_THEMES: tuple[Theme, ...] = (
    Theme(
        name="opencode",
        primary="#58a6ff",
        secondary="#79c0ff",
        accent="#d2a8ff",
        foreground="#e6edf3",
        background="#0d1117",
        surface="#161b22",
        panel="#21262d",
        warning="#d29922",
        error="#f85149",
        success="#3fb950",
        dark=True,
        variables={
            "block-cursor-background": "#58a6ff",
            "footer-background": "#161b22",
        },
    ),
    Theme(
        name="matrix",
        primary="#00ff66",
        secondary="#00cc55",
        accent="#33ff99",
        foreground="#b8ffc8",
        background="#050805",
        surface="#0a120a",
        panel="#101a10",
        warning="#c8ff00",
        error="#ff3355",
        success="#00ff66",
        dark=True,
    ),
    Theme(
        name="mercury",
        primary="#7eb8da",
        secondary="#a8c5d4",
        accent="#c9a0dc",
        foreground="#e8eef2",
        background="#1a1d21",
        surface="#22262b",
        panel="#2a2f36",
        warning="#e0b15c",
        error="#e06c75",
        success="#7fd99a",
        dark=True,
    ),
    Theme(
        name="nightowl",
        primary="#82aaff",
        secondary="#c792ea",
        accent="#7fdbca",
        foreground="#d6deeb",
        background="#011627",
        surface="#0b2942",
        panel="#112635",
        warning="#ffcb8b",
        error="#ef5350",
        success="#addb67",
        dark=True,
    ),
    Theme(
        name="orng",
        primary="#ff8c42",
        secondary="#ffb347",
        accent="#ffd166",
        foreground="#fff3e6",
        background="#1a120c",
        surface="#241810",
        panel="#2e1f14",
        warning="#ffb347",
        error="#ff5e5b",
        success="#8fdb6e",
        dark=True,
    ),
    Theme(
        name="osaka-jade",
        primary="#5fb3a1",
        secondary="#7ec8b8",
        accent="#a8d5ba",
        foreground="#e6f2ee",
        background="#0e1614",
        surface="#15201c",
        panel="#1c2a25",
        warning="#d4a574",
        error="#e07a7a",
        success="#5fb3a1",
        dark=True,
    ),
    Theme(
        name="opencode-go",
        primary="#4ecdc4",
        secondary="#95e1d3",
        accent="#f38181",
        foreground="#eaeaea",
        background="#121212",
        surface="#1c1c1c",
        panel="#262626",
        warning="#fce38a",
        error="#f38181",
        success="#4ecdc4",
        dark=True,
    ),
)

# Prefer showing these first (OpenCode-like list), then the rest alphabetically.
_PREFERRED_ORDER: tuple[str, ...] = (
    "opencode",
    "opencode-go",
    "nord",
    "tokyo-night",
    "monokai",
    "dracula",
    "catppuccin-mocha",
    "gruvbox",
    "atom-one-dark",
    "nightowl",
    "matrix",
    "mercury",
    "orng",
    "osaka-jade",
    "rose-pine",
    "solarized-dark",
    "flexoki",
)


def register_opensquad_themes(app: Any) -> None:
    """Register custom themes on a Textual App (safe to call once per app)."""
    for theme in _CUSTOM_THEMES:
        try:
            app.register_theme(theme)
        except Exception:
            # Already registered / older Textual — ignore
            pass


def list_theme_names(app: Any) -> list[str]:
    """Ordered theme names available on this app."""
    available = set(getattr(app, "available_themes", {}) or {})
    ordered: list[str] = []
    for name in _PREFERRED_ORDER:
        if name in available:
            ordered.append(name)
    for name in sorted(available):
        if (
            name not in ordered
            and not name.startswith("textual-light")
            and "latte" not in name
            and "dawn" not in name
            and name != "ansi-light"
        ):
            # Prefer dark themes in the picker; still allow light if user wants later
            theme = app.get_theme(name)
            if theme is not None and getattr(theme, "dark", True):
                ordered.append(name)
    # Append light themes at the end
    for name in sorted(available):
        if name not in ordered:
            ordered.append(name)
    return ordered


def load_saved_theme() -> str:
    try:
        if THEME_PREF_PATH.is_file():
            data = json.loads(THEME_PREF_PATH.read_text(encoding="utf-8"))
            name = str((data or {}).get("theme") or "").strip()
            if name:
                return name
    except Exception:
        pass
    return DEFAULT_THEME


def save_theme(name: str) -> None:
    name = (name or "").strip()
    if not name:
        return
    try:
        THEME_PREF_PATH.parent.mkdir(parents=True, exist_ok=True)
        THEME_PREF_PATH.write_text(
            json.dumps({"theme": name}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
