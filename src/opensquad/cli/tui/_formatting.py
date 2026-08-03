"""Formatting helpers for the OpenSquad TUI (extracted from app.py)."""

from __future__ import annotations

import sys
from pathlib import Path


def same_reply(a: str, b: str) -> bool:
    """True if two reply strings are the same turn (exact / whitespace-normalized).

    Do NOT treat prefix/substring as equal — streamed text may miss the last
    gateway debounce chunk while the final event is complete (Web prefers final).
    """
    if not a or not b:
        return False
    if a == b:
        return True
    na = " ".join(a.split())
    nb = " ".join(b.split())
    return bool(na and nb and na == nb)


def truncated_prefix(short: str, full: str) -> bool:
    """True when ``short`` looks like an incomplete stream of ``full``."""
    if not short or not full:
        return False
    s, f = short.strip(), full.strip()
    return bool(s and f.startswith(s) and len(f) > len(s))


class FormattingMixin:
    """Mixin methods moved from cli/tui/app.py (see app.py for the app class)."""

    @staticmethod
    def _approx_tokens(text: str) -> int:
        import re

        raw = text or ""
        cjk = sum(1 for ch in raw if ord(ch) > 0x2E80)
        words = len(re.findall(r"\S+", raw))
        return max(cjk, words)

    def _thinking_label_hex(self) -> str:
        """OpenCode-style Thinking label: muted tan, lower chroma than warning amber."""
        return "#a6926a"

    @staticmethod
    def _tool_line_failed(text: str) -> bool:
        low = (text or "").lower()
        return any(
            k in low
            for k in (
                "fail",
                "error",
                "exception",
                "traceback",
                "denied",
                "timeout",
                "timed out",
                "✗",
                "❌",
            )
        )

    def _escape_markup(self, text: str) -> str:
        return text.replace("[", "\\[")

    @staticmethod
    def _fmt_duration(secs: float) -> str:
        """Live clock. Integer seconds on Windows to cut prompt-meta paint churn."""
        secs = max(0.0, float(secs or 0.0))
        if secs < 60:
            if sys.platform == "win32":
                return f"{int(secs)}s"
            return f"{secs:.1f}s"
        total = int(secs)
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        if h:
            return f"{h}h{m}m{s}s"
        return f"{m}m{s}s"

    @staticmethod
    def _fmt_tokens_smooth(n: int) -> str:
        """Token count for ↑↓ meter — use K from 1000+ (same scale as context)."""
        n = max(0, int(n or 0))
        if n >= 1_000_000:
            return f"{n / 1_000_000:.2f}M"
        if n >= 1000:
            return f"{n / 1000:.1f}K"
        return str(n)

    @staticmethod
    def _short_path(path: str, max_len: int = 48) -> str:
        p = (path or "").strip() or "—"
        home = str(Path.home())
        # Windows paths differ in drive-letter case (C:\ vs c:\)
        if sys.platform == "win32":
            if p.lower().startswith(home.lower()):
                p = "~" + p[len(home) :]
        elif p.startswith(home):
            p = "~" + p[len(home) :]
        if len(p) <= max_len:
            return p
        keep = max_len - 1
        left = max(8, keep // 2)
        right = keep - left
        return p[:left] + "…" + p[-right:]

    def _display_project_path(self, path: str | None = None, max_len: int = 72) -> str:
        """Path for the welcome card — always readable (never a lone '~')."""
        raw = (path or getattr(self, "_launch_cwd", None) or self._resolve_project_path() or "").strip()
        if not raw:
            return "—"
        short = self._short_path(raw, max_len=max_len)
        if short in ("~", "~/", "~\\"):
            return raw
        return short

    @staticmethod
    def _fmt_tokens(n: int) -> str:
        n = max(0, int(n or 0))
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1000:
            return f"{n / 1000:.1f}K"
        return str(n)

    def _theme_hex(self, key: str, fallback: str) -> str:
        """Resolve a Textual theme CSS variable to a hex color for Rich markup."""
        try:
            val = (self.get_css_variables() or {}).get(key) or fallback
            s = str(val).strip()
            if s.startswith("#") and len(s) >= 4:
                return s.split()[0]
        except Exception:
            pass
        return fallback

    def _looks_like_agent_prose(self, text: str) -> bool:
        t = (text or "").strip()
        if not t or t.startswith(("  ⚙", "  ✓", "  ·", "[", "/")):
            return False
        try:
            from opensquad.cli.tui.md_table import has_markdown_table

            return has_markdown_table(t)
        except Exception:
            return False

    def _approx_out_tokens(self, piece: str) -> int:
        """Rough ↓ token delta for a streamed text piece (CJK≈1, ascii≈/4)."""
        text = piece or ""
        if not text:
            return 0
        cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
        ascii_n = len(text) - cjk
        delta = cjk + (ascii_n // 4)
        if delta <= 0 and text.strip():
            return 1
        return max(0, delta)
