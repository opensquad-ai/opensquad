"""Unit-ish: Ctrl+O detail blocks store + markup expand/collapse."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    from opensquad.cli.api_client import GatewayClient
    from opensquad.cli.tui.app import _build_app_class

    App = _build_app_class()
    app = App(client=GatewayClient(), agent="agent301", no_start=True)

    # Stash tool detail as bridge would
    app._on_tool_detail("system__get_system_info", "c1", "os=Windows\nram=16G")
    assert app._tool_detail_pending["id:c1"].startswith("os=")
    assert app._take_tool_detail("system__get_system_info", "✓ system__get_system_info#c1") == "os=Windows\nram=16G"
    assert app._take_tool_detail("system__get_system_info", "✓ system__get_system_info#c1") == ""

    # Markup: collapsed vs expanded
    app._detail_expanded = False
    compact = app._tool_markup_parts("result", "my_tool", "(a=1)\nhello world result", "done")
    assert compact is not None
    assert "hello" not in compact
    assert "my_tool" in compact

    app._detail_expanded = True
    expanded = app._tool_markup_parts("result", "my_tool", "(a=1)\nhello world result", "done")
    assert expanded is not None
    assert "hello" in expanded
    assert "my_tool" in expanded

    # Thinking fold
    long_body = "词" * 200
    app._detail_expanded = False
    folded = app._thinking_markup(long_body, live=False)
    assert "思考" in folded or "Thinking" in folded
    assert "…（^O" in folded or "^O" in folded or "…" in folded

    app._detail_expanded = True
    full = app._thinking_markup(long_body, live=False)
    assert long_body[:50] in full.replace("\\", "")

    print("PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("FAIL", e)
        raise
