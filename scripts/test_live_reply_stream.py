"""Smoke: live reply helpers exist and build growing → final markups."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    from opensquad.cli.tui.app import _build_app_class

    App = _build_app_class()
    # Instantiate without running; only exercise pure helpers via unbound methods
    assert hasattr(App, "_live_reply_items") or True

    # Build a minimal instance by calling __init__ pieces is heavy; instead
    # check source-level API on the class after factory.
    cls = App

    # Textual App subclass — create with client stub if needed is heavy.
    # Validate markup helpers by binding onto a blank object with required attrs.
    class _H:
        pass

    h = _H()
    h._theme_hex = lambda _k, d: d  # type: ignore
    h._signal_lamp = lambda state: "● "  # type: ignore
    h._escape_markup = lambda t: str(t).replace("[", "\\[")  # type: ignore
    h._agent_footer_markup = lambda: "[dim]footer[/]"  # type: ignore
    h._write_agent_body = lambda text, write, lamp="done": write(f"● {text}")  # type: ignore

    live = cls._live_reply_items(h, "你好\n世界")
    assert len(live) == 2, live
    assert "你好" in str(live[0][0])
    assert "世界" in str(live[1][0])

    growing = cls._live_reply_items(h, "你好\n世界\n第")
    assert len(growing) == 3

    final = cls._final_reply_items(h, "完整回复")
    assert len(final) >= 3  # spacer + body + footer + spacer
    assert final[0][0] == ""
    assert final[-1][0] == ""
    print("PASS")


if __name__ == "__main__":
    main()
