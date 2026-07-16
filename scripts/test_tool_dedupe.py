"""Regression: duplicate tool_result / premature finalize must paint one green lamp."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def test_bridge_tool_result_call_id_dedupe() -> None:
    from opensquad.cli.api_client import GatewayClient
    from opensquad.cli.commands.chat_cmd import AgentBridge

    bridge = AgentBridge(GatewayClient(gateway_url="http://127.0.0.1:9"), "agent301", interactive=True)
    lines: list[str] = []
    bridge.on_line = lines.append

    call_id = "call_1200_system__get_system_info_0"
    bridge._handle_message(
        {
            "type": "tool_call",
            "data": {"id": call_id, "name": "system__get_system_info", "args": "{}"},
        }
    )
    # Same result delivered 5 times (WS redelivery / double path)
    for i in range(5):
        bridge._handle_message(
            {
                "type": "tool_result",
                "data": {
                    "id": call_id,
                    "name": "system__get_system_info",
                    "result": f'{{"status":"success","n":{i}}}',
                },
            }
        )

    calls = [x for x in lines if "⚙" in x]
    results = [x for x in lines if "✓" in x]
    assert len(calls) == 1, calls
    assert len(results) == 1, results
    assert "system__get_system_info" in results[0]
    assert call_id in results[0]
    print("bridge_dedupe_ok", results[0])


def test_tui_claim_blocks_duplicate_results() -> None:
    from opensquad.cli.api_client import GatewayClient
    from opensquad.cli.tui.app import _build_app_class

    App = _build_app_class()
    app = App(client=GatewayClient(gateway_url="http://127.0.0.1:9"), agent="agent301", no_start=True)

    raw_call = "  ⚙ system__get_system_info#cid1({})"
    raw_result = "  ✓ system__get_system_info#cid1"
    assert app._claim_tool_line(raw_call) is True
    assert app._claim_tool_line(raw_result) is True
    # 4 more identical results must be rejected
    for _ in range(4):
        assert app._claim_tool_line(raw_result) is False

    # New call with new id may complete again
    assert app._claim_tool_line("  ⚙ system__get_system_info#cid2({})") is True
    assert app._claim_tool_line("  ✓ system__get_system_info#cid2") is True
    assert app._claim_tool_line("  ✓ system__get_system_info#cid2") is False
    print("tui_claim_ok")


def test_parse_strips_call_id_from_display_name() -> None:
    from opensquad.cli.api_client import GatewayClient
    from opensquad.cli.tui.app import _build_app_class

    App = _build_app_class()
    app = App(client=GatewayClient(gateway_url="http://127.0.0.1:9"), agent="a", no_start=True)
    kind, name, detail, state = app._parse_tool_line("  ✓ system__get_system_info#call_1")
    assert kind == "result"
    assert name == "system__get_system_info"
    assert state == "done"
    kind2, name2, _, _ = app._parse_tool_line("  ⚙ system__get_system_info#call_1({})")
    assert kind2 == "call"
    assert name2 == "system__get_system_info"
    print("parse_ok", name, name2)


if __name__ == "__main__":
    test_bridge_tool_result_call_id_dedupe()
    test_tui_claim_blocks_duplicate_results()
    test_parse_strips_call_id_from_display_name()
    print("PASS")
